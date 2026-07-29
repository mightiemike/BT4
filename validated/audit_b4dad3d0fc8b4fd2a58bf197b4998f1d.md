Confirmed: `AccountInitDecorator` runs *before* `SetPubKeyDecorator` in the ante chain and, on the new-account/gasless path, returns early ("bypasses the rest of the ante chain") — it never reaches `ante.NewSetPubKeyDecorator`. `SetPubKeyDecorator` is normally what binds a signature's public key to the address that is supposed to own it (`sdk.AccAddress(pk.Address()).Equals(signer)`). Because that check never runs on the new-account path, `verifySignatureForNewAccount` only proves that *some* signature under *some* `sig.PubKey` verifies over the tx bytes — it never proves `sig.PubKey`'s derived address equals `newAccAddr` (`signers[0]`, taken straight from the message's declared `Signer`/`Owner` field). [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Gasless first-use account creation binds a signature to an arbitrary claimed address without verifying the signer's pubkey actually derives it - (File: `app/ante/account_init_decorator.go`)

### Summary
This is the same bug class as the Trail-of-Bits Uniswap finding: a cryptographic artifact (there, ciphertext; here, a signature) is stored/consumed keyed by a "context" (there, a public address; here, `newAccAddr`) that the artifact itself never cryptographically binds to. `AccountInitDecorator.verifySignatureForNewAccount` builds a `SignerData{Address: newAccAddr, ...}` and calls `authsigning.VerifySignature`, but never checks that `sig.PubKey`'s derived address equals `newAccAddr`. That binding is normally supplied by `ante.NewSetPubKeyDecorator`, which this decorator explicitly runs *before* and short-circuits past on the new-account path.

### Finding Description
For gasless transactions (`app/txpolicy/gasless.go` whitelist: `MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`), when the declared signer address has no on-chain account yet, `AccountInitDecorator.AnteHandle` takes `newAccAddr := signers[0]` (the address is whatever the message's proto-declared `Signer`/`Owner`/UV field says — an attacker-controlled string) and calls `verifySignatureForNewAccount`. That function constructs `SignerData{Address: newAccAddr, ChainID: chainID, AccountNumber: 0, Sequence: 0, PubKey: <the tx's own sig.PubKey>}` and calls `authsigning.VerifySignature`. `VerifySignature` proves the signed bytes (tx body + auth info, including account_number=0/sequence=0 and, for SIGN_MODE_DIRECT, `PubKey` in `AuthInfo`) are consistent with `sig.PubKey`. It does **not** verify that `sig.PubKey`'s address (`sdk.AccAddress(pk.Address())`) equals `newAccAddr`. That specific check is `ante.NewSetPubKeyDecorator`'s job — and it runs strictly after `AccountInitDecorator` in the chain, and is never reached because the new-account branch returns immediately (`return ctx, nil`) after calling `acc.SetSequence(1)` / `aid.ak.SetAccount(ctx, acc)`.

Consequently, an attacker fully controlling their own keypair can submit a gasless message (e.g. `MsgVoteInbound`) whose `Signer`/validator-identity field names an address they do not own the key for, sign the tx with their own unrelated key, and the decorator will happily create an on-chain `BaseAccount` for that victim address with `sequence=1` (instead of `0`), then move to the next decorator (which for a brand-new account still proceeds because `HasAccount` is now true on any retry). This is reachable purely with an ordinary unprivileged transaction submission — no compromised keys, no malicious validator/relayer required.

### Impact Explanation
This is squarely a "state safety / admission" issue matching the report's scope (gasless allowlisting / ante checks / first-use account initialization turning attacker input into accepted authorization). Concrete corrupted values:
- An arbitrary address's account object is force-created with `sequence=1` before its real owner ever transacts, silently changing the expected initial sequence number the real owner's wallet/signer software will assume (typically clients assume sequence starts at `0` for a brand-new account). This can break a legitimate first transaction from that address (its self-signed tx built with sequence 0 will now fail sequence verification), a denial-of-service against onboarding for arbitrary addresses (including specific Universal Validators the attacker wants to grief) — reachable by any unprivileged party.
- Because the message's actual authorization for the *content* (e.g., a UV vote) is enforced downstream by bonded/tombstoned checks in `x/uvalidator`/`x/uexecutor`, the attacker cannot forge a vote's *effect*; the concrete damage here is account-state corruption (wrong initial sequence, account existence) for an address the attacker doesn't control, not fund theft. This still fits the "state safety" allowed-impact category (persistence of invalid canonical account state reachable by an ordinary unprivileged user).

### Likelihood Explanation
High — no privileged access, no validator/relayer collusion, and no race condition is needed. Any address can be targeted as long as it currently has no on-chain account and the attacker can craft a gasless-whitelisted message that declares that address as its `Signer`.

### Recommendation
In `verifySignatureForNewAccount` (or before it, in `AccountInitDecorator.AnteHandle`), explicitly verify that the signature's public key derives to `newAccAddr` before creating the account — i.e., replicate the check that `ante.NewSetPubKeyDecorator` performs (`sdk.AccAddress(pubKey.Address()).Equals(newAccAddr)`) prior to calling `authsigning.VerifySignature`, and reject the tx if it doesn't match. This restores the "bind signed artifact to its claimed context" property the ordering comment ("must be called before ... SetPubKeyDecorator") currently breaks.

### Proof of Concept
1. Attacker generates keypair `K_attacker`.
2. Attacker crafts a gasless-whitelisted message (e.g. `MsgVoteInbound`) whose `Signer` field (the field `GetSigners()` resolves) is set to victim address `Addr_V` (an address for which no on-chain account exists yet, e.g. a freshly-keygen'd UV hot key that hasn't transacted).
3. Attacker signs the transaction with `K_attacker` (SignerInfo carries `K_attacker`'s pubkey; account_number/sequence fields set to 0/0 as required for a fresh account).
4. `AccountInitDecorator.AnteHandle` sees `!ak.HasAccount(ctx, Addr_V)` is true, calls `verifySignatureForNewAccount`, which builds `SignerData{Address: Addr_V, AccountNumber:0, Sequence:0, PubKey: K_attacker}` and calls `authsigning.VerifySignature` — this succeeds because the signature is self-consistent with `K_attacker` over the tx bytes; no check ties `K_attacker` to `Addr_V`.
5. The decorator creates a `BaseAccount` for `Addr_V` with `sequence = 1` and returns, short-circuiting the rest of ante (never reaching `SetPubKeyDecorator`).
6. `Addr_V`'s legitimate owner later tries their genuine first transaction assuming `sequence=0`; it is rejected as a sequence mismatch, or, depending on downstream message-specific authorization, the account is left in an attacker-influenced state.

### Citations

**File:** app/ante/ante_cosmos.go (L43-50)
```go
		// NewAccountInitDecorator must be called before all signature verification decorators and SetPubKeyDecorator
		// - this
		// 1. generates the account for the new accounts only for gasless transactions,
		// 2. verifies the sig, and
		// 3. bypasses the rest of the ante chain
		NewAccountInitDecorator(options.AccountKeeper, options.SignModeHandler),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
```

**File:** app/ante/account_init_decorator.go (L52-75)
```go
	newAccAddr := signers[0]
	if !aid.ak.HasAccount(ctx, newAccAddr) {
		ctx.Logger().Debug("account init decorator: new account detected on gasless tx, verifying signature",
			"address", sdk.AccAddress(newAccAddr).String(),
			"simulate", simulate,
		)
		// if account does not exist on chain, bypass rest of ante chain (especially gas and signature verification) here.
		// Perform signature verification on account number e and sequence number e instead.
		if err := aid.verifySignatureForNewAccount(ctx, tx, simulate); err != nil {
			ctx.Logger().Debug("account init decorator: signature verification failed for new account",
				"address", sdk.AccAddress(newAccAddr).String(),
				"error", err,
			)
			return ctx, err
		}

		acc := aid.ak.NewAccountWithAddress(ctx, newAccAddr)
		acc.SetSequence(1)
		aid.ak.SetAccount(ctx, acc)
		ctx.Logger().Info("account init decorator: new account created via gasless tx",
			"address", sdk.AccAddress(newAccAddr).String(),
		)
		return ctx, nil
	}
```

**File:** app/ante/account_init_decorator.go (L106-143)
```go
	newAccAddr := sdk.AccAddress(signers[0])
	for _, sig := range sigs {
		pubKey := sig.PubKey
		if pubKey == nil {
			return errorsmod.Wrap(sdkerrors.ErrInvalidPubKey, "pubkey is not provided in signature")
		}

		// retrieve signer data
		chainID := ctx.ChainID()
		var accSequence uint64 = 0
		var accNum uint64 = 0

		// no need to verify signatures on recheck tx
		if !simulate && !ctx.IsReCheckTx() && ctx.IsSigverifyTx() {
			anyPk, _ := codectypes.NewAnyWithValue(pubKey)

			signerData := txsigning.SignerData{
				Address:       newAccAddr.String(),
				ChainID:       chainID,
				AccountNumber: accNum,
				Sequence:      accSequence,
				PubKey: &anypb.Any{
					TypeUrl: anyPk.TypeUrl,
					Value:   anyPk.Value,
				},
			}
			adaptableTx, ok := tx.(authsigning.V2AdaptableTx)
			if !ok {
				return fmt.Errorf("expected tx to implement V2AdaptableTx, got %T", tx)
			}
			txData := adaptableTx.GetSigningTxData()
			ctx.Logger().Debug("account init decorator: verifying signature for new account",
				"address", newAccAddr.String(),
				"chain_id", chainID,
				"acc_num", accNum,
				"sequence", accSequence,
			)
			err = authsigning.VerifySignature(ctx, pubKey, signerData, sig.Data, aid.signModeHandler, txData)
```
