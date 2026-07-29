This confirms the mechanics. Now finalizing analysis:

The attack path works exactly as described: `MsgVoteTssKeyProcess.ValidateBasic` at [1](#0-0)  only checks bech32 format and non-empty strings/non-zero process_id — no bonded-validator check. Since this message type is in the gasless allowlist at [2](#0-1) , `AccountInitDecorator.AnteHandle` at [3](#0-2)  will, for any never-before-seen signer address, verify the self-consistent signature (which any attacker-controlled keypair can produce) and then unconditionally call `aid.ak.NewAccountWithAddress` / `SetAccount` at lines 68-70, persisting a `BaseAccount` regardless of whether the message will later succeed. The actual bonded-UV gate only exists downstream in `msgServer.VoteTssKeyProcess` via `IsBondedUniversalValidator` at [4](#0-3) , which runs during message execution — after the ante chain (and its state writes) has already been committed to the block's state, per Cosmos SDK `runTx` semantics (ante-handler cache is written independent of message-execution success/failure).

So yes: an unbonded, unprivileged, brand-new-keypair attacker can force the chain to persist a new `BaseAccount` at zero token cost, once per fresh keypair per transaction, even though the underlying vote is always rejected as "not bonded." This is a genuine account-creation-without-cost bug reachable via ordinary transaction submission — not requiring any privileged role. The gate that's supposed to reserve gasless treatment for legitimate first-time UV hot keys (per the design intent documented in `app/README.md:180-182`) is not actually enforced before the costly state mutation (account creation) happens.

Whether this rises to a "material," non-network-level DoS is a judgment call: each abuse still requires broadcasting and including one transaction per new account (bounded by block gas/size and requiring a valid signature), so it is not "free" in the sense of network amplification, but it is free in **token cost**, and it persists a permanent (never removed) `BaseAccount` entry in the `AccountKeeper`/IAVL store for every fresh keypair the attacker chooses to burn a tx slot on — a genuine unbounded, cost-free state-growth vector distinct from mempool-only flooding.

### Title
Cost-free account creation via gasless `MsgVoteTssKeyProcess` from unbonded addresses causes unbounded persisted state bloat - (`app/ante/account_init_decorator.go`)

### Summary
`AccountInitDecorator` creates and persists a new `BaseAccount` for any signer of a gasless-whitelisted message — including `MsgVoteTssKeyProcess` — before the message-level bonded-Universal-Validator check ever runs, letting any unprivileged party mint free, permanent account state that is never cleaned up even though the underlying vote always fails.

### Finding Description
`IsGaslessTx` treats `MsgVoteTssKeyProcess` (and other UV-only vote messages) as gasless purely based on message type, with no check that the sender is an actual bonded UV: [5](#0-4) . `AccountInitDecorator.AnteHandle` then unconditionally creates a `BaseAccount` for any never-seen signer of a gasless tx after a self-verified signature check, and short-circuits the remainder of the ante chain: [6](#0-5) . `MsgVoteTssKeyProcess.ValidateBasic` performs no authorization/bonded check, only bech32 and non-empty-field validation: [1](#0-0) . The actual authorization gate (`IsBondedUniversalValidator`) lives only in the message handler, which runs after ante-handler state has already been committed: [4](#0-3) .

### Impact Explanation
An attacker with no funds and no validator bond can permanently create arbitrary numbers of `BaseAccount` entries in chain state at zero fee cost, one per fresh keypair per submitted transaction, growing the account-keeper/IAVL state indefinitely. This is a state-bloat concern rather than fund loss, unauthorized execution, or consensus divergence.

### Likelihood Explanation
High for anyone willing to broadcast transactions and pay only for block inclusion (no token cost); bounded only by block space/gas and node processing capacity, not by any authorization check.

### Recommendation
Gate `AccountInitDecorator`'s account-creation path (or `IsGaslessTx` itself) for UV-only vote message types on membership in the current/pending Universal Validator set (or another cheap on-chain check) before persisting a new account, rather than deferring authorization entirely to the message handler.

### Proof of Concept
As described in the question: loop-generate N fresh keypairs; for each, build and broadcast a gasless `MsgVoteTssKeyProcess` tx (valid bech32 signer, non-empty `tss_pubkey`/`key_id`, non-zero `process_id`) signed by that keypair; observe that `AccountInitDecorator` creates and commits a `BaseAccount` (sequence=1) for each in `AccountKeeper` even though every corresponding `VoteTssKeyProcess` call returns `"universal validator for signer %s is not bonded"` from `x/utss/keeper/msg_server.go:75-77`.

### Citations

**File:** x/utss/types/msg_tss_key_process.go (L29-46)
```go
func (msg *MsgVoteTssKeyProcess) ValidateBasic() error {
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "invalid signer address")
	}

	if strings.TrimSpace(msg.TssPubkey) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "tss_pubkey cannot be empty")
	}

	if strings.TrimSpace(msg.KeyId) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "key_id cannot be empty")
	}

	if msg.ProcessId == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "process_id must be greater than 0")
	}

	return nil
```

**File:** app/txpolicy/gasless.go (L14-48)
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
```

**File:** app/ante/account_init_decorator.go (L31-81)
```go
func (aid AccountInitDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	if !txpolicy.IsGaslessTx(tx) {
		// Skip account initialization for non-gasless transactions
		ctx.Logger().Debug("account init decorator: non-gasless tx, skipping account init")
		return next(ctx, tx, simulate)
	}

	sigTx, ok := tx.(authsigning.Tx)
	if !ok {
		return ctx, errorsmod.Wrap(sdkerrors.ErrTxDecode, "invalid transaction type")
	}

	signers, err := sigTx.GetSigners()
	if err != nil || len(signers) != 1 {
		ctx.Logger().Debug("account init decorator: could not get unique signer, passing to next handler",
			"num_signers", len(signers),
			"error", err,
		)
		return next(ctx, tx, simulate)
	}

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

	ctx.Logger().Debug("account init decorator: existing account on gasless tx, passing to next handler",
		"address", sdk.AccAddress(newAccAddr).String(),
	)
	return next(ctx, tx, simulate)
}
```

**File:** x/utss/keeper/msg_server.go (L71-77)
```go
	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}
```
