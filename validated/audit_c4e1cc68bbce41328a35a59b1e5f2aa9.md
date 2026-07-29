### Title
Missing pubkey-to-signer address binding check in `AccountInitDecorator.verifySignatureForNewAccount` allows unprivileged account squatting and nonce corruption for arbitrary addresses - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator.AnteHandle` initializes brand-new accounts for gasless transactions by manually verifying a signature and then creating the account with `Sequence = 1`, bypassing the rest of the ante chain (including `SetPubKeyDecorator`). The manual verification never checks that the provided public key actually hashes to the claimed signer address, so any attacker can "create" an on-chain account for an arbitrary target address while signing with their own unrelated keypair.

### Finding Description
For a gasless transaction (`txpolicy.IsGaslessTx`), if the signer address has no existing account, `AccountInitDecorator.AnteHandle` skips the standard ante chain entirely and calls `verifySignatureForNewAccount` itself: [1](#0-0) 

Inside `verifySignatureForNewAccount`, the code extracts `pubKey := sig.PubKey` and calls `authsigning.VerifySignature` with `signerData.Address = newAccAddr.String()`, `AccountNumber = 0`, `Sequence = 0`: [2](#0-1) 

Crucially, this function never checks `pubKey.Address().Equals(newAccAddr)` — the binding check that the standard `x/auth` `SetPubKeyDecorator` normally performs before allowing a pubkey to be associated with an account. `authsigning.VerifySignature` only confirms that the signature over the sign bytes is cryptographically valid for the given `pubKey`; for `SIGN_MODE_DIRECT`/`SIGN_MODE_TEXTUAL` the sign bytes are derived from `body_bytes` + `auth_info_bytes` + `chain_id` + `account_number`, none of which cryptographically tie the pubkey to the specific bech32 address carried in the message body (the signer address comes from the message's declared `cosmos.msg.v1.signer` field, e.g., `Signer`/`Creator` in `MsgVoteInbound`, `MsgVoteOutbound`, `MsgExecutePayload`, etc., not from the pubkey itself).

As a result, an attacker can:
1. Craft a gasless message (e.g., `MsgVoteInbound`) whose signer field is set to any arbitrary, not-yet-used address `X` (which they do not control the private key for — e.g., a future user's address, or a known/predictable Universal Validator address).
2. Sign the transaction with their own, completely unrelated keypair.
3. Submit it. `verifySignatureForNewAccount` verifies the attacker's own valid signature but never checks it corresponds to `X`, so verification succeeds.
4. `aid.ak.NewAccountWithAddress(ctx, newAccAddr)` creates a `BaseAccount` for `X`, and `acc.SetSequence(1)` is called — but no pubkey is ever stored on the account (unlike the normal `SetPubKeyDecorator` flow, which calls `acc.SetPubKey(pk)`).

This corrupts the nonce/account state for address `X`: the account now exists with `Sequence = 1` despite its true owner never having transacted. When the legitimate owner of `X` later submits their real first transaction (expecting `Sequence = 0`, standard SDK client assumption for an unregistered account), it will be rejected due to sequence mismatch, denying that user the ability to bootstrap their account without manual recovery.

### Impact Explanation
This directly corrupts account nonce progression for addresses the attacker does not control, which is an explicitly listed in-scope impact ("corruption of ... nonce progression"). It also produces an unprivileged, non-network-level denial of service against any user (or even a predictable Universal Validator address) whose account is squatted before their first real transaction. If any gasless-message business logic elsewhere trusts "account exists with sequence > 0" as a weak signal of prior legitimate activity, this could compound into further state confusion, though the primary confirmed impact is nonce/account-state corruption and account-bootstrap DoS for a targeted address.

### Likelihood Explanation
High feasibility: the attacker only needs to know or predict a target address (public information, e.g. addresses derived from known validator keys or expected user addresses) and can craft the message and signature entirely with their own keys — no privileged access, relayer, validator, or admin role is required. The flaw is a straightforward missing address/pubkey binding check that every other pubkey-setting path in `x/auth` (`SetPubKeyDecorator`) enforces but this custom fast-path omits.

### Recommendation
In `verifySignatureForNewAccount`, after extracting `pubKey`, explicitly verify `sdk.AccAddress(pubKey.Address()).Equals(newAccAddr)` (mirroring `SetPubKeyDecorator`'s check) before accepting the signature as valid for account initialization, and reject with `sdkerrors.ErrInvalidPubKey` on mismatch. Additionally, persist the verified pubkey onto the newly created account (`acc.SetPubKey(pubKey)`) so that the initialized account state is consistent with normal `x/auth` account initialization.

### Proof of Concept
1. Choose a target address `X` that the attacker does not control (e.g., a not-yet-active address, predictable UV address).
2. Construct a gasless `MsgVoteInbound` (or any message in `txpolicy.GaslessMsgTypes`) with its signer field set to `X`.
3. Sign the transaction using attacker's own private key/pubkey `P` (unrelated to `X`).
4. Submit the transaction. `AccountInitDecorator.AnteHandle` sees `!ak.HasAccount(ctx, X)`, calls `verifySignatureForNewAccount`, which validates the attacker's own signature using `pubKey = P` and never checks `P.Address() == X`.
5. Observe that `X` now exists on-chain with `Sequence = 1` and no stored pubkey, despite the attacker never possessing `X`'s private key.
6. Have the legitimate owner of `X` later submit their real first transaction with `Sequence = 0` (standard assumption for an unregistered account) and observe it is rejected due to sequence mismatch — confirming the griefing/DoS effect on the legitimate account owner's onboarding.

### Citations

**File:** app/ante/account_init_decorator.go (L52-74)
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
