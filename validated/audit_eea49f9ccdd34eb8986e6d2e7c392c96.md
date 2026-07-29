### Title
Excess gas-fee refund is permanently skipped when the funds-revert mint fails, causing silent, unrecoverable loss of the user's prepaid destination-chain gas fee - (File: x/uexecutor/keeper/outbound.go)

### Summary
`handleFailedOutbound` in `x/uexecutor/keeper/outbound.go` is the Push Chain analog of the Taiko `Bridge`/`QuotaManager` fee-loss bug class: when the destination-chain leg of a `UniversalTx` fails, the keeper must both (a) re-mint the bridged PRC20 back to the sender and (b) refund the unused portion of the destination gas fee (`gasFee - gasFeeUsed`) via `applyGasRefund`. If step (a) fails, the function returns early via `AbortOutbound` and never reaches `applyGasRefund`, so the fee refund that was supposed to run "regardless of tx type ... whether the execution succeeded or failed" is dropped and never retried anywhere else in the codebase.

### Finding Description
`FinalizeOutbound` [1](#0-0)  routes a failed destination-chain observation to `handleFailedOutbound`. For funds-carrying tx types, that function attempts to re-mint the bridged PRC20 to the revert recipient via `CallPRC20Deposit`: [2](#0-1) 

If `CallPRC20Deposit` returns an error, the function calls `k.AbortOutbound(...)` and **returns immediately**, never executing the subsequent lines that run `k.applyGasRefund(ctx, &outbound, obs)`: [3](#0-2) 

The comment directly above `applyGasRefund` in the success path states the intended invariant: *"Refund excess gas regardless of tx type — gas was consumed on the external chain whether the execution succeeded or failed."* [4](#0-3)  That invariant is violated on the abort branch: `AbortOutbound` only sets `Status_ABORTED` and an `AbortReason`, removes the pending index entry, and emits a monitoring event — it does not itself perform or schedule a gas refund [5](#0-4) . `Status_ABORTED` is a terminal state accepted by `OutboundTx.ValidateBasic` with no further validation or reprocessing hook [6](#0-5) , and grep across the repo shows no code path that re-invokes `applyGasRefund` for an aborted outbound.

This is structurally the same defect as the reported Taiko bug: a message/outbound enters a failure/limit-hit branch (there: `QuotaManager` max reached → retriable/failed; here: PRC20 re-mint failure → `ABORTED`), and the associated fee that should be returned to the user is silently and permanently lost instead of being refunded or the whole operation reverting atomically.

### Impact Explanation
Any ordinary user whose outbound is a `FUNDS`, `GAS_AND_PAYLOAD`, or `FUNDS_AND_PAYLOAD` type, and whose destination-chain execution is voted as failed by honest UVs with `gasFeeUsed < gasFee` (a very common, non-adversarial situation — most reverted destination-chain calls consume less than the full allocated gas), will have their excess gas fee become permanently unreachable if the compensating PRC20 mint on Push Chain fails for any reason (e.g., token/mint constraints, transient EVM/module issues). The user's principal can potentially still be recovered by an admin via the separate `RESCUE_FUNDS`/manual-intervention path implied by the `AbortReason`, but the prepaid gas-fee refund is not part of that recovery flow and is lost with no on-chain remediation. This is a fund-loss (permanent loss of a portion of user funds) issue in the universal-execution/refund-accounting invariant area explicitly called out in scope ("permanent loss ... of user or protocol-controlled funds", "corruption of ... gas fee accounting, refund accounting").

### Likelihood Explanation
The trigger condition (destination execution fails with excess unused gas) is common and requires no privileged access — it can occur from ordinary cross-chain usage whenever the destination call reverts (e.g. slippage, insufficient balance at destination, contract revert) while under its assigned gas budget. The only additional condition needed to lose the fee (rather than just delay it) is that `CallPRC20Deposit` also fails during the revert — this is less certain to be attacker-controllable, and I could not fully verify from the indexed code which specific conditions cause `CallPRC20Deposit` to fail (e.g., PRC20 supply caps, pausability, or other on-chain constraints), since the PRC20/UniversalCore contract source was not available in the indexed context. This uncertainty affects how easily an unprivileged actor can reliably force the mint-failure branch, but the missing-refund defect itself is unconditional once that branch is hit, regardless of cause.

### Recommendation
Restructure `handleFailedOutbound` so that `applyGasRefund` is always invoked for the observed outbound — regardless of whether the funds re-mint succeeds — mirroring the explicit intent already documented in the code comment. Concretely: move the `k.applyGasRefund(ctx, &outbound, obs)` call before the early-return/`AbortOutbound` branch (or call it unconditionally in `FinalizeOutbound`/`handleFailedOutbound` prior to any abort), and update `AbortOutbound`/`OutboundTx` to retain enough state (e.g. `pc_refund_execution`) so that a subsequent admin recovery flow does not need to separately re-derive or forfeit the gas-fee refund. Additionally, consider making the mint-failure and gas-refund attempts independent operations (each recorded and retried independently) so a failure in one does not prevent the other from running.

### Proof of Concept
1. A user submits a cross-chain `FUNDS` transfer that results in a Push Chain `UniversalTx` with an `OutboundTx` of `TxType_FUNDS`, `GasFee = 111`, `GasToken` set.
2. UVs observe the destination-chain execution as failed with `GasFeeUsed = 50` (61 excess) via `MsgVoteOutbound`, driving `FinalizeOutbound` → `handleFailedOutbound` [1](#0-0) .
3. In `handleFailedOutbound`, `CallPRC20Deposit` (the re-mint of bridged tokens for the revert) fails — e.g., due to a PRC20-side constraint being hit.
4. The function returns via `k.AbortOutbound(...)` at line 135-136 [7](#0-6)  — `k.applyGasRefund` on line 158 is never reached.
5. The outbound is now `Status_ABORTED`; `PcRefundExecution` remains `nil` permanently, and no other code path in the repository re-invokes `applyGasRefund` for this outbound ID. The user's 61-unit excess gas fee is unrecoverable through any on-chain mechanism, contrasting with the `TestGasFeeRefund` integration test's explicit assertion that "excess gas must be refunded even when outbound failed" [8](#0-7)  — which only covers the successful-remint branch, not the abort branch.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L45-69)
```go
// AbortOutbound marks an outbound as ABORTED with a reason.
// This signals that automatic processing has failed and manual intervention is needed.
func (k Keeper) AbortOutbound(ctx context.Context, utxId string, outbound types.OutboundTx, reason string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	outbound.OutboundStatus = types.Status_ABORTED
	outbound.AbortReason = reason

	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Defensively remove from pending index (may already be removed by caller)
	_ = k.PendingOutbounds.Remove(ctx, outbound.Id)

	// Emit event for monitoring/alerting
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"outbound_aborted",
		sdk.NewAttribute("utx_id", utxId),
		sdk.NewAttribute("outbound_id", outbound.Id),
		sdk.NewAttribute("abort_reason", reason),
	))

	return nil
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

**File:** x/uexecutor/keeper/outbound.go (L102-137)
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
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/keeper/outbound.go (L149-161)
```go
	outbound.OutboundStatus = types.Status_REVERTED
	k.Logger().Info("outbound reverted",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)

	// Refund excess gas regardless of tx type — gas was consumed on the external
	// chain whether the execution succeeded or failed.
	k.applyGasRefund(ctx, &outbound, obs)

	return k.UpdateOutbound(ctx, utxId, outbound)
}
```

**File:** x/uexecutor/types/outbound_tx.go (L150-151)
```go
	case Status_ABORTED:
		// Set internally by AbortOutbound — no external validation needed.
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L199-239)
```go
	t.Run("failed outbound performs both revert and gas refund", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// gasFee = 111 (mock), gasFeeUsed = 50 → 61 excess to refund.
		// Both the bridged funds revert AND the excess gas refund must happen.
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteOutbound(
				t,
				ctx,
				app,
				vals[i],
				coreAcc,
				utxId,
				outbound,
				false,
				"execution failed",
				"50", // gasFeeUsed=50 < gasFee=111 → 61 excess
			)
			require.NoError(t, err)
		}

		utx, _, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)

		ob := utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_REVERTED, ob.OutboundStatus)

		// Revert: bridged funds minted back
		require.NotNil(t, ob.PcRevertExecution)
		require.Equal(t, "SUCCESS", ob.PcRevertExecution.Status)

		// Gas refund: excess gas must also be returned on failure
		require.NotNil(t, ob.PcRefundExecution,
			"excess gas must be refunded even when outbound failed")
		require.NotEmpty(t, ob.PcRefundExecution.Status)
	})
```
