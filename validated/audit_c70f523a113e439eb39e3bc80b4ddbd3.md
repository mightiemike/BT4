Confirmed. `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta` msg-server handlers authorize purely by string-matching `msg.Signer` against a bonded Universal Validator record via `ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)` [1](#0-0) , with the actual `msg.Signer` string derived solely from the field an attacker fully controls in the message itself, and `GetSigners()` just parses that same field back to bytes [2](#0-1) . This confirms the ante-layer authentication gap is exploitable at the message-handler layer.

### Title
Unauthenticated impersonation of any first-use gasless-transaction signer via missing pubkey-to-address binding in `AccountInitDecorator` — (File: `app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator` — the "initial position" of any Push Chain account — creates a brand-new on-chain account for a gasless-tx signer address and treats the transaction as authenticated, without ever checking that the supplied signature's public key actually hashes to that signer address. Because this decorator runs *before*, and short-circuits, `SetPubKeyDecorator`/`SigVerificationDecorator` in the ante chain [3](#0-2) , that binding check never happens anywhere else in the pipeline for a first-use address. An unprivileged attacker can therefore self-sign a gasless tx whose `Signer` field names *any address that has not yet sent a transaction* (e.g. a not-yet-active Universal Validator hot key, or any other yet-untouched address) and have the chain accept it as a legitimately authenticated action from that address.

### Finding Description
`AccountInitDecorator.AnteHandle` only fires for gasless-whitelisted message types (`MsgExecutePayload`, `MsgVoteInbound`, `MsgVoteOutbound`, `MsgVoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`, `MsgMigrateUEA`) [4](#0-3) . When the tx's sole signer has no on-chain account yet, it calls `verifySignatureForNewAccount`, which builds a `SignerData{Address: newAccAddr, AccountNumber: 0, Sequence: 0, PubKey: <pubkey embedded in the same tx's signature>}` and calls `authsigning.VerifySignature` [5](#0-4) .

This only proves that the *supplied pubKey* produced the *supplied signature* over a doc that happens to mention `newAccAddr` as a text field — it never proves `sdk.AccAddress(pubKey.Address()) == newAccAddr`. Since both the pubkey and signature are supplied by the attacker in the same transaction, an attacker can trivially self-sign with their own keypair while writing an arbitrary victim/target bech32 string into the message's `Signer` field. The decorator then creates the account at that address (`acc.SetSequence(1)`, no pubkey stored) and **returns immediately without calling `next`**, bypassing `SetPubKeyDecorator` and `SigVerificationDecorator` entirely for this transaction [6](#0-5) . The ante chain overall succeeds, so `baseapp` proceeds to execute the message normally.

Downstream, message handlers such as `VoteInbound`, `VoteOutbound`, and `VoteChainMeta` authorize solely by checking whether the attacker-supplied `msg.Signer` string corresponds to a bonded Universal Validator, with no cryptographic tie-back to who actually holds that validator's key [1](#0-0) . The `Signer` used for this authorization check is exactly the same attacker-controlled field consumed by `GetSigners()` [2](#0-1) , i.e. the very field the ante layer failed to bind cryptographically.

### Impact Explanation
This is a native analog of the "insolvent initial position" bug class: the very first state transition establishing an account/identity is accepted without adequate validation of the counterpart's genuine ownership, permanently corrupting downstream invariants that depend on that identity being authentic. Concretely:

- Any address that is a **bonded Universal Validator but has never yet submitted a transaction** (e.g., freshly onboarded UVs, or hot keys rotated but not yet used) can have its identity hijacked by an unprivileged attacker to submit forged `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta` votes, injecting bogus votes into honest-validator ballot finalization for inbound/outbound/chain-meta state — directly impacting the "Honest-validator finalization path" invariant in scope.
- The forged account creation also poisons the victim's real sequence number (set to `1` with a null pubkey), so when the legitimate key holder later submits their genuine first transaction, `SigVerificationDecorator`'s sequence check will conflict, causing denial-of-service/lockout for the legitimate validator's onboarding.
- The same primitive extends to `MsgVoteTssKeyProcess`/`MsgVoteFundMigration` (TSS coordination) and `MsgExecutePayload`/`MsgMigrateUEA` (universal execution), for any address in those flows that has not yet transacted.

### Likelihood Explanation
Exploitation requires no privilege beyond broadcasting a transaction: pick any target bech32 address known to be untouched on-chain (trivially discoverable — e.g. any freshly generated UV hot-key address before its first vote, since UV key material/address is typically known/announced ahead of activation), self-sign a gasless message naming that address as `Signer`, and broadcast. No validator collusion, key compromise, or race condition beyond "target hasn't sent tx #1 yet" is needed.

### Recommendation
In `verifySignatureForNewAccount` (`app/ante/account_init_decorator.go`), before/while verifying the signature, explicitly assert that `sdk.AccAddress(pubKey.Address()).Equals(newAccAddr)` for every signer, mirroring what `SetPubKeyDecorator` does for existing accounts. Only after that binding check succeeds should the decorator proceed to create the account and short-circuit the remaining ante chain.

### Proof of Concept
1. Identify (or predict) an address `V` that is registered as a bonded Universal Validator (or otherwise privileged in a gasless flow) but has not yet submitted any transaction (`HasAccount(ctx, V) == false`).
2. Attacker generates their own keypair `(skA, pkA)`.
3. Attacker constructs a `MsgVoteInbound` (or `MsgVoteOutbound`/`MsgVoteChainMeta`) with `Signer = V`, and any desired forged inbound/outbound/chain-meta payload.
4. Attacker signs the tx envelope with `skA`/`pkA` — `GetSigners()` returns `V`, but the signature's embedded pubkey is `pkA`, unrelated to `V`.
5. Submit the tx. `AccountInitDecorator.AnteHandle` sees `!ak.HasAccount(ctx, V)`, calls `verifySignatureForNewAccount`, which verifies `pkA` signed the doc naming `Address: V` — succeeds, because no check ties `pkA` to `V`.
6. The decorator creates account `V` (`sequence=1`), returns success, ante chain completes.
7. `baseapp` executes `MsgVoteInbound`; `msg_server.go`'s `IsBondedUniversalValidator(ctx, V)` returns true (since `V` is a real bonded UV in the registry) — the forged vote is recorded as coming from `V`, despite the attacker never possessing `V`'s private key.

### Citations

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

**File:** x/uexecutor/types/msg_vote_inbound.go (L46-50)
```go
// GetSigners returns the expected signers for a MsgVoteInbound message.
func (msg *MsgVoteInbound) GetSigners() []sdk.AccAddress {
	addr, _ := sdk.AccAddressFromBech32(msg.Signer)
	return []sdk.AccAddress{addr}
}
```

**File:** app/ante/ante_cosmos.go (L43-54)
```go
		// NewAccountInitDecorator must be called before all signature verification decorators and SetPubKeyDecorator
		// - this
		// 1. generates the account for the new accounts only for gasless transactions,
		// 2. verifies the sig, and
		// 3. bypasses the rest of the ante chain
		NewAccountInitDecorator(options.AccountKeeper, options.SignModeHandler),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
```

**File:** app/txpolicy/gasless.go (L14-49)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)

	msgs := tx.GetMsgs()
	if len(msgs) == 0 {
		return false
	}

	for _, msg := range msgs {
		switch m := msg.(type) {
		case *authz.MsgExec:
			// Only gasless if ALL inner messages are allowed
			for _, innerMsg := range m.Msgs {
				if !slices.Contains(GaslessMsgTypes, innerMsg.TypeUrl) {
					return false
				}
			}
		default:
			if !slices.Contains(GaslessMsgTypes, sdk.MsgTypeURL(msg)) {
				return false
			}
		}
	}
	return true
}
```

**File:** app/ante/account_init_decorator.go (L60-75)
```go
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

**File:** app/ante/account_init_decorator.go (L106-159)
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
```
