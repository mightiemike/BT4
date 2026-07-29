## Analysis

The external report's bug class is: **a security-critical verification step is skipped/bypassed, so an unverified/uncontrolled component ends up trusted for authentication of secure communication.** The native analog in Push Chain is the custom `AccountInitDecorator`, which bypasses the standard Cosmos SDK pubkey-to-address binding check when initializing a brand-new account for a gasless transaction.

### Root cause

In the normal Cosmos SDK ante pipeline, `ante.NewSetPubKeyDecorator` cryptographically binds the transaction's signer address to the signature's public key by checking `pubKey.Address() == signers[i]` before any message executes. Push Chain's custom pipeline explicitly documents that `AccountInitDecorator` must run *before* `SetPubKeyDecorator` and, for a not-yet-existing account on a gasless tx, it **short-circuits the entire remaining ante chain** (it returns `ctx, nil` directly instead of calling `next`): [1](#0-0) 

Its own signature check, `verifySignatureForNewAccount`, only proves that the supplied signature is cryptographically valid for the supplied `pubKey` over `(chainID, accNum=0, seq=0, body, authInfo)`. It never checks that `sdk.AccAddress(pubKey.Address())` equals the account address being initialized: [2](#0-1) 

Meanwhile, `GetSigners()` for the gasless message types derives the acting address purely from the message's own `signer` string field — completely independent of which key actually produced the signature: [3](#0-2) 

### Exploitability

Downstream authorization for these gasless messages (e.g. `VoteInbound`) trusts `msg.Signer` as the acting validator identity and only checks bonded/tombstoned status — it performs no additional per-message cryptographic binding to the tx signature: [4](#0-3) 

So any unprivileged attacker who knows a bonded Universal Validator's Cosmos address (public on-chain data from the UV set) can, **before that validator's account has sent its first tx**, craft and sign a gasless tx (e.g. `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`) with `Signer` set to the victim UV's address, but sign it with a completely unrelated keypair the attacker owns. `AccountInitDecorator` verifies the attacker's own self-consistent signature, creates the account under the victim's address, and short-circuits the chain — the message then executes as if it were cast by the legitimate UV, corrupting ballot/TSS/migration voting state.

### Title
Missing pubkey-to-signer-address binding in `AccountInitDecorator` allows forged first-vote impersonation of unbonded/fresh Universal Validator accounts - (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator.verifySignatureForNewAccount` cryptographically verifies a signature against a fixed `(chainID, accNum=0, seq=0)` signing doc but never checks that the signature's public key hashes to the account address it is initializing, and it short-circuits the ante chain so the standard `SetPubKeyDecorator` address-binding check is never reached.

### Finding Description
`GetSigners()` for gasless message types (`MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`) is populated entirely from the message's own bech32 `signer` field, not from the signature. `AccountInitDecorator` runs before `SetPubKeyDecorator`/`SigVerificationDecorator` and, for accounts that do not yet exist, performs its own signature check that omits the `pubKey.Address() == signer` comparison that `SetPubKeyDecorator` normally enforces, then returns success directly, bypassing every subsequent ante decorator including the one that would otherwise catch the mismatch.

### Impact Explanation
An unprivileged attacker can impersonate any bonded Universal Validator (or any UEA account) whose account has not yet been initialized on-chain, injecting forged inbound/outbound/chain-meta votes, TSS process votes, or fund-migration votes that are then processed as if cast by an honest, bonded validator — corrupting ballot finalization state, TSS/migration state, and universal execution outcomes with a completely unprivileged, honest-validator-independent trigger.

### Likelihood Explanation
Exploitability is gated on the target account not yet existing on-chain, which is exactly the window the decorator was built for ("lets a freshly-keygen'd Universal Validator hot key vote on its very first tx"). An attacker monitoring UV bonding events can race to submit a forged first vote before the legitimate key does, and validator/UEA addresses are public on-chain data.

### Recommendation
In `verifySignatureForNewAccount`, after successful cryptographic verification, explicitly assert `sdk.AccAddress(pubKey.Address()).Equals(newAccAddr)` before creating the account, mirroring the check performed by the standard `SetPubKeyDecorator`.

### Proof of Concept
1. Observe a newly bonded Universal Validator address `V` (from `x/uvalidator` state) that has not yet submitted any transaction.
2. Attacker generates their own keypair `K` and constructs a `MsgVoteInbound{Signer: V, Inbound: ...}` (or another gasless message type), wrapped in a tx signed with `K`, `account_number=0`, `sequence=0`.
3. `AccountInitDecorator.verifySignatureForNewAccount` validates the signature against `K` successfully (chainID/accNum/seq match), creates the account for address `V`, and returns success without checking that `K`'s derived address equals `V`.
4. `msgServer.VoteInbound` sees `msg.Signer == V`, finds `V` bonded via `IsBondedUniversalValidator`, and records the forged vote as if cast by the legitimate validator `V`.

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

**File:** app/ante/account_init_decorator.go (L106-167)
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
			if err != nil {
				var errMsg string
				if OnlyLegacyAminoSigners(sig.Data) {
					// If all signers are using SIGN_MODE_LEGACY_AMINO, we rely on VerifySignature to check account sequence number,
					// and therefore communicate sequence number as a potential cause of error.
					errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d), sequence (%d) and chain-id (%s)", accNum, accSequence, chainID)
				} else {
					errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d) and chain-id (%s): (%s)", accNum, chainID, err.Error())
				}
				ctx.Logger().Debug("account init decorator: signature invalid for new account",
					"address", newAccAddr.String(),
					"chain_id", chainID,
				)
				return errorsmod.Wrap(sdkerrors.ErrUnauthorized, errMsg)

			}
		} else {
			ctx.Logger().Debug("account init decorator: skipping signature verification",
				"address", newAccAddr.String(),
				"simulate", simulate,
				"is_recheck_tx", ctx.IsReCheckTx(),
				"is_sigverify_tx", ctx.IsSigverifyTx(),
			)
		}
```

**File:** x/uexecutor/types/msg_vote_inbound.go (L46-50)
```go
// GetSigners returns the expected signers for a MsgVoteInbound message.
func (msg *MsgVoteInbound) GetSigners() []sdk.AccAddress {
	addr, _ := sdk.AccAddressFromBech32(msg.Signer)
	return []sdk.AccAddress{addr}
}
```

**File:** x/uexecutor/keeper/msg_server.go (L72-97)
```go
// VoteInbound implements types.MsgServer.
func (ms msgServer) VoteInbound(ctx context.Context, msg *types.MsgVoteInbound) (*types.MsgVoteInboundResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	// Convert account to validator operator address
	signerValAddr := sdk.ValAddress(signerAccAddr)

	// Lookup the linked universal validator for this signer
	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
	}
```
