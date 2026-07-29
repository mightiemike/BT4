### Title
Failed revert re-mint in `handleFailedOutbound` leaves outbound permanently stuck in `OBSERVED` while `computeUniversalStatus` reports it as `OUTBOUND_SUCCESS` - (File: x/uexecutor/keeper/outbound.go)

### Summary
This is a native analog of the Arbitrum `edgeAtOneStepProof` bug: an operation that should drive a state machine to a specific terminal state instead triggers a code path that returns an error *before* the terminal-state assignment happens, leaving the record permanently parked in a non-terminal, non-restartable intermediate state.

### Finding Description
`VoteOutbound` moves an outbound from `PENDING` to `OBSERVED` and immediately removes it from `PendingOutbounds` (the secondary index used to retry/track in-flight outbounds), then calls `FinalizeOutbound` to drive it to its true terminal state: [1](#0-0) 

`FinalizeOutbound` dispatches to `handleFailedOutbound` when the UV-observed result was unsuccessful: [2](#0-1) 

Inside `handleFailedOutbound`, for `FUNDS`/`GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD` outbound types, the code parses `outbound.Amount` as a big integer and returns an error immediately if parsing fails — **before** `outbound.OutboundStatus` is ever set to `Status_REVERTED` or `Status_ABORTED`: [3](#0-2) 

Because this error is returned out of `handleFailedOutbound` → `FinalizeOutbound` → back to `VoteOutbound`, the caller only logs it and stores it as `RevertError` on the parent `UniversalTx` — it never re-attempts the state transition, and the outbound's `OutboundStatus` remains whatever it was set to just before `FinalizeOutbound` was invoked, i.e. `Status_OBSERVED`: [4](#0-3) 

The outbound is now permanently stuck: it is no longer in `PendingOutbounds` (already removed at line 127 of `msg_vote_outbound.go`), it is not `PENDING` (so `VoteOutbound`'s guard blocks any retry/re-vote path since it rejects non-`PENDING` outbounds), and it never became `REVERTED` or `ABORTED` (so no compensating re-mint of bridged funds ever runs). This is structurally identical to the reported bug: a failure mid-transition leaves the state machine in a state (`OBSERVED`) that is neither the intended terminal state (`REVERTED`) nor recoverable/re-enterable.

Compounding this, the module's own status-rollup function treats any non-`PENDING`, non-`REVERTED` outbound as a success: [5](#0-4) 

So a UTX whose outbound actually failed on the destination chain and never had its bridged funds re-minted back on Push Chain will be reported via `computeUniversalStatus` as `OUTBOUND_SUCCESS`, masking the fact that funds are stuck.

### Impact Explanation
If the code path that hits this early-return error is reachable without privileged actors, this results in a fund-loss/fund-freezing bug matching the "In scope" impact class: bridged tokens that should be re-minted back to the sender/refund recipient on a failed outbound are never re-minted, and there is no automated or on-chain retry path since the outbound is removed from `PendingOutbounds` and blocked from re-voting. The corrupted value is `OutboundTx.outbound_status` (stuck at `OBSERVED` instead of `REVERTED`) and the derived `computeUniversalStatus` result, which falsely reports success, obscuring the loss from any monitoring built on the module's canonical status API.

### Likelihood Explanation
I could not conclusively determine, within the available index, whether `outbound.Amount` (and by extension the `SetString` parse failure) can ever be attacker-influenced by an ordinary unprivileged user through the default deposit/payload path, or whether it is always derived deterministically and validated earlier in the inbound-execution pipeline (where the amount originates from `Inbound.Amount`, itself a `string` field populated from UV-voted inbound data — see `proto/uexecutor/v1/types.proto:107-119`). If `Amount` is always numeric by construction before an outbound is created, this specific failure branch may be unreachable in practice, making the bug latent/defensive-only rather than exploitable. I was unable to locate and fully trace the outbound-creation code path (`CreateOutbound`/equivalent) in the time available to confirm whether the `Amount` string is validated/sanitized at creation time or copied through un-validated from user-controlled payload data.

### Recommendation
- **Short term:** In `handleFailedOutbound` (x/uexecutor/keeper/outbound.go:102-118), on `SetString` failure (or any other early-return error in this function), transition the outbound to `Status_ABORTED` with a descriptive `AbortReason` (mirroring the existing `AbortOutbound` pattern used later in the same function for re-mint failures) instead of returning a bare error that leaves `OutboundStatus` unchanged. Also fix `computeUniversalStatus` to treat `Status_ABORTED` explicitly rather than implicitly falling into the `OUTBOUND_SUCCESS` bucket.
- **Long term:** Audit every early-return path in `FinalizeOutbound`, `handleFailedOutbound`, and `handleSuccessfulOutbound` to guarantee that any error causes a defined terminal (or explicitly retryable) status transition, and add a regression/property test asserting that no outbound can end a `VoteOutbound` call in `OBSERVED` status with a non-nil error recorded on the parent UTX.

### Proof of Concept
Conceptual PoC (I could not fully verify reachability of attacker-controlled `Amount` due to index limitations, so this is presented as the mechanical trigger, contingent on that verification):
1. An inbound producing an outbound of `TxType_FUNDS` (or `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`) is created with `OutboundTx.Amount` set to a non-numeric string (if this is reachable from user-supplied payload data without independent validation at outbound-creation time).
2. UVs vote the outbound as failed (`OutboundObservation.Success = false`) via `MsgVoteOutbound`, reaching threshold.
3. `VoteOutbound` sets `OutboundStatus = Status_OBSERVED`, removes the entry from `PendingOutbounds` (x/uexecutor/keeper/msg_vote_outbound.go:111-129), then calls `FinalizeOutbound` → `handleFailedOutbound`.
4. `amount.SetString(outbound.Amount, 10)` fails, and the function returns `fmt.Errorf("invalid amount: %s", ...)` immediately (x/uexecutor/keeper/outbound.go:114-118), skipping the `outbound.OutboundStatus = types.Status_REVERTED` assignment and the re-mint call entirely.
5. `VoteOutbound` catches the error and only stores it in `UniversalTx.RevertError` (x/uexecutor/keeper/msg_vote_outbound.go:134-145); the outbound record permanently keeps `OutboundStatus = OBSERVED`.
6. Any subsequent call to `GetUniversalTx`/`computeUniversalStatus` reports `OUTBOUND_SUCCESS` for this UTX despite the failed re-mint (x/uexecutor/keeper/query_server.go:65-88), and no further on-chain process retries the revert since the outbound is no longer in `PendingOutbounds` and `VoteOutbound`'s `Status_PENDING` guard blocks any corrective re-vote.

### Citations

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L110-129)
```go
	// Step 5: Update outbound state to OBSERVED
	outbound.OutboundStatus = types.Status_OBSERVED
	outbound.ObservedTx = &observedTx

	k.Logger().Info("outbound observed",
		"utx_id", utxId,
		"outbound_id", outboundId,
		"success", observedTx.Success,
		"dest_chain", outbound.DestinationChain,
	)

	// Persist the state inside UniversalTx
	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Remove from pending outbounds index now that status is OBSERVED
	if err := k.PendingOutbounds.Remove(ctx, outboundId); err != nil {
		return fmt.Errorf("failed to remove pending outbound index for %s: %w", outboundId, err)
	}
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L131-146)
```go
	// Step 6: Finalize outbound (refund if failed).
	// If re-mint fails, handleFailedOutbound marks it ABORTED internally and returns nil.
	// Business logic errors are stored in RevertError on the UTX; only infra errors are returned.
	if err := k.FinalizeOutbound(ctx, utxId, outbound); err != nil {
		k.Logger().Error("outbound finalization error stored on utx",
			"utx_id", utxId,
			"outbound_id", outboundId,
			"error", err.Error(),
		)
		if storeErr := k.UpdateUniversalTx(ctx, utxId, func(u *types.UniversalTx) error {
			u.RevertError = err.Error()
			return nil
		}); storeErr != nil {
			return storeErr
		}
	}
```

**File:** x/uexecutor/keeper/outbound.go (L71-97)
```go
func (k Keeper) FinalizeOutbound(ctx context.Context, utxId string, outbound types.OutboundTx) error {
	// If not observed yet, do nothing
	if outbound.OutboundStatus != types.Status_OBSERVED {
		return nil
	}

	obs := outbound.ObservedTx
	if obs == nil {
		return nil
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Info("finalizing outbound",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"success", obs.Success,
		"dest_chain", outbound.DestinationChain,
		"tx_type", outbound.TxType.String(),
	)

	if !obs.Success {
		return k.handleFailedOutbound(sdkCtx, utxId, outbound, obs)
	}

	return k.handleSuccessfulOutbound(sdkCtx, utxId, outbound, obs)
}
```

**File:** x/uexecutor/keeper/outbound.go (L102-118)
```go
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
```

**File:** x/uexecutor/keeper/query_server.go (L65-88)
```go
// Priority: outbounds > PC txs > inbound presence.
func computeUniversalStatus(utx *types.UniversalTx) types.UniversalTxStatus {
	if len(utx.OutboundTx) > 0 {
		anyPending := false
		anyReverted := false
		for _, ob := range utx.OutboundTx {
			if ob == nil {
				continue
			}
			switch ob.OutboundStatus {
			case types.Status_PENDING:
				anyPending = true
			case types.Status_REVERTED:
				anyReverted = true
			}
		}
		if anyPending {
			return types.UniversalTxStatus_OUTBOUND_PENDING
		}
		if anyReverted {
			return types.UniversalTxStatus_OUTBOUND_FAILED
		}
		return types.UniversalTxStatus_OUTBOUND_SUCCESS
	}
```
