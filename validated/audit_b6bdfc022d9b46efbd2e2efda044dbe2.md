## Analysis

The claim is accurate. `handleFailedInboundValidation` [1](#0-0)  is reached when `ValidateForExecution` fails after ballot finalization, at which point the UTX already exists [2](#0-1) . It calls `buildRevertOutbound` unconditionally and passes the result straight to `attachOutboundsToUtx`, which always sets `OutboundStatus_PENDING` and writes a `PendingOutbounds` entry [3](#0-2) [4](#0-3) .

By contrast, the normal outbound-creation path (`BuildOutboundsFromReceipt`) explicitly checks `IsChainOutboundEnabled` and refuses to create an outbound (returning an error, no PENDING state) when outbound is disabled for the destination chain [5](#0-4) . The revert path (used identically by `handleFailedInboundValidation`, `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, and `ExecuteInboundGas(AndPayload)`) has no equivalent check [6](#0-5) [7](#0-6) .

Meanwhile, `IsChainInboundEnabled` is validated at vote time [8](#0-7) , and `IsChainInboundEnabled`/`IsChainOutboundEnabled` are independent boolean flags in `ChainEnabled` [9](#0-8) , so a chain config with inbound enabled and outbound disabled is a valid, admin-settable state (e.g., pausing outbound due to an issue on the destination gateway while still accepting deposits) rather than a malicious/abusive one.

Downstream, both the TSS session manager and the tx broadcaster explicitly refuse to move a PENDING outbound forward once created for a disabled chain — `verifyOutboundSigningRequest` rejects signing [10](#0-9)  and `broadcastOutbound` skips broadcasting [11](#0-10)  — confirming that a revert outbound created under this condition is permanently stuck at `Status_PENDING` with no path to resolution (no `ABORTED`/retry fallback exists for this case; `ABORTED` is only used when `attachOutboundsToUtx` itself errors) [12](#0-11) .

Because `handleFailedInboundValidation` fires when validation fails *before* any PRC20 is minted on Push Chain (validation happens ahead of execution in `VoteInbound`) [13](#0-12) , the user's originally-deposited source-chain funds are neither refunded nor represented on Push Chain — they are permanently unrecoverable once this revert outbound is orphaned.

### Title
Revert-outbound creation path never checks `IsChainOutboundEnabled`, permanently freezing user refunds - (File: `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/handle_failed_inbound_validation.go`)

### Summary
`buildRevertOutbound`/`attachOutboundsToUtx`, used by all inbound-revert paths (including `handleFailedInboundValidation`), create a `Status_PENDING` `INBOUND_REVERT` outbound without checking whether outbound processing is enabled for the destination (source) chain, unlike the normal outbound-creation path in `BuildOutboundsFromReceipt`.

### Finding Description
`VoteInbound` only checks `IsChainInboundEnabled` before accepting votes. If the chain registry later has (or already has) `IsInboundEnabled=true, IsOutboundEnabled=false` — a legitimate independent config combination — and a user's inbound fails `ValidateForExecution` (or any subsequent execution step) for any reason, `handleFailedInboundValidation`/`ExecuteInboundFunds`/etc. call `buildRevertOutbound` and `attachOutboundsToUtx` unconditionally, creating a `PENDING` revert `OutboundTx` and a `PendingOutbounds` index entry. Neither TSS signing (`verifyOutboundSigningRequest`) nor broadcasting (`broadcastOutbound`) will ever process this outbound because both explicitly check `IsChainOutboundEnabled` and refuse. The outbound is left permanently `PENDING` with no automatic remediation.

### Impact Explanation
This corrupts the outbound status invariant (a `PENDING` outbound that can never be signed/broadcast/observed) and results in permanent freezing of the user's refund — the underlying source-chain funds already locked in the gateway are never credited back to the user, and since the failure occurred pre-deposit, no compensating PRC20 was minted on Push Chain either. This is unauthorized/uncompensated fund loss for the affected user, within the "permanent freezing... of user... funds" impact category.

### Likelihood Explanation
Requires only a normal, non-malicious registry state (outbound disabled, inbound enabled for a chain) plus an ordinary user inbound that fails validation/execution — no privileged or malicious actor action is needed beyond the pre-existing admin config choice, which is a supported, independent flag combination in the schema.

### Recommendation
Gate `buildRevertOutbound`/`attachOutboundsToUtx` revert-outbound creation with the same `IsChainOutboundEnabled` check used in `BuildOutboundsFromReceipt`. When disabled, either set the resulting outbound/UTX to `ABORTED` with a clear error (so it's queryable and can be resolved via admin remediation) instead of leaving it silently `PENDING` forever.

### Proof of Concept
1. Registry: set `ChainConfig.Enabled = {IsInboundEnabled: true, IsOutboundEnabled: false}` for chain `X`.
2. Submit/vote a valid inbound from chain `X` that fails `ValidateForExecution` (e.g., malformed payload) so ballot finalizes and `handleFailedInboundValidation` runs.
3. Observe the UTX now contains an `OutboundTx` with `TxType=INBOUND_REVERT`, `OutboundStatus=Status_PENDING`, and a `PendingOutbounds` entry exists for it.
4. Confirm no TSS signing session or broadcast ever processes it (both check `IsChainOutboundEnabled` and reject), so the entry remains `PENDING` indefinitely and the user's funds are never returned.

### Citations

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L41-65)
```go
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
			// Store the revert failure reason on the UTX so it's queryable on-chain.
			// The FAILED PCTx is already recorded above — this adds why the revert wasn't attached.
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
				utx.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				// UpdateUniversalTx only fails on infra issues — return to roll back and retry
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L136-146)
```go
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-25)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-57)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L76-86)
```go
	if err != nil && !inbound.IsCEA {
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
		if attachErr := k.attachOutboundsToUtx(sdkCtx, utx.Id, []*types.OutboundTx{revertOutbound}, err.Error()); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, utx.Id, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L192-208)
```go
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** proto/uregistry/v1/types.proto (L88-96)
```text
// ChainEnabled defines if chain is enabled for inbound as well as outbound
message ChainEnabled {
  option (amino.name) = "uregistry/chain_enabled";
  option (gogoproto.equal) = true;
  option (gogoproto.goproto_stringer) = false;

  bool isInboundEnabled = 1;
  bool isOutboundEnabled = 2;
}
```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L920-923)
```go
	// Reject signing if outbound is disabled for the destination chain
	if sm.chains != nil && !sm.chains.IsChainOutboundEnabled(chainID) {
		return fmt.Errorf("outbound disabled for chain %s, refusing to sign", chainID)
	}
```

**File:** universalClient/tss/txbroadcaster/broadcaster.go (L119-124)
```go
	chainID := data.DestinationChain
	if !b.chains.IsChainOutboundEnabled(chainID) {
		b.logger.Warn().Str("chain", chainID).Str("event_id", event.EventID).
			Msg("outbound disabled, skipping broadcast")
		return
	}
```

**File:** x/uexecutor/README.md (L152-161)
```markdown
### `Status` — per-outbound status

`OutboundTx.outbound_status` uses a separate, narrower enum:

| `Status` | Meaning |
|---|---|
| `PENDING` | Outbound created on Push Chain, waiting for UVs to broadcast and vote |
| `OBSERVED` | UVs voted the outbound was successfully broadcast on the destination chain |
| `REVERTED` | UVs voted the outbound permanently failed; revert path triggered |
| `ABORTED` | Finalization or revert attachment failed and requires manual intervention |
```
