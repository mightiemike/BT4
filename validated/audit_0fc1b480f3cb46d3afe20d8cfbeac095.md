Based on my investigation, this confirms the analog is genuine and well-documented in code and tests. `applyGasRefund` never returns an error and silently swallows failures — the failure is only recorded on `PcRefundExecution.Status = "FAILED"` [1](#0-0) , and the design is explicitly confirmed by the integration test comment "refund failure does not revert the outbound" [2](#0-1) . There is no retry mechanism anywhere in the codebase, and the outbound is already permanently removed from `PendingOutbounds` before `FinalizeOutbound`/`applyGasRefund` even runs [3](#0-2) .

### Title
Silent, unrecoverable loss of excess-gas refunds due to missing balance/success verification before terminal outbound state and queue removal - (File: x/uexecutor/keeper/outbound.go)

### Summary
The `removeNodeDelegatorContractFromQueue` bug class — removing an accounting entry from a tracked queue without verifying the underlying balance/settlement actually completed — has a direct analog in `x/uexecutor`. When an outbound is voted `OBSERVED` by validators, it is immediately removed from the `PendingOutbounds` index (Step 5 in `VoteOutbound`) before `FinalizeOutbound` performs the fund-moving side effects (bridged-fund re-mint and excess-gas refund) [4](#0-3) . The gas-refund leg, `applyGasRefund`, has no error return value at all — any failure of `CallUniversalCoreRefundUnusedGas` (swap or no-swap) is only recorded as a string status field on the `OutboundTx`, and the outbound is nonetheless marked terminal (`REVERTED` or left `OBSERVED`) and persisted [5](#0-4) .

### Finding Description
`FinalizeOutbound` dispatches to `handleFailedOutbound` or `handleSuccessfulOutbound` depending on the destination-chain observation [6](#0-5) . Both paths call `applyGasRefund` to return any excess gas fee (`gasFee - gasFeeUsed`) to the user [7](#0-6) . Unlike the bridged-funds re-mint path in `handleFailedOutbound`, which on failure calls `AbortOutbound` to mark the outbound `ABORTED` for manual intervention [8](#0-7) , `applyGasRefund` has a `void` (no-error) signature. If both the swap-based refund and the no-swap fallback (`CallUniversalCoreRefundUnusedGas`) fail — e.g., because the module account lacks a sufficient PRC20 gas-token or native balance — the function only sets `refundPcTx.Status = "FAILED"` and returns normally [1](#0-0) . The caller then proceeds to `UpdateOutbound`, committing the outbound to its terminal status (`REVERTED` for failed outbounds, or leaving `OBSERVED` for successful ones) regardless of the refund outcome [9](#0-8) . Because `PendingOutbounds.Remove` already executed before `FinalizeOutbound` ran [10](#0-9) , there is no operator-facing pending-queue entry left to signal that a balance-affecting settlement is outstanding — the only trace is the `RefundSwapError`/`PcRefundExecution.Status` string field embedded deep in the UTX record. This mirrors the reported analog: an accounting/withdrawal-queue entry is finalized/removed without verifying that the corresponding balance movement actually succeeded.

### Impact Explanation
This causes a genuine, permanent loss of user funds (the excess gas-fee refund) with no automated recovery path and no queue-based tracking mechanism, matching the "permanent loss of protocol/user-controlled funds" impact category. Because an ordinary unprivileged relayer/user flow (an outbound gas cost simply coming in under `gasFee`, combined with any transient EVM/PRC20 failure on the refund call, e.g. insufficient module liquidity for the swap route or gas-token balance) is enough to trigger it, this is reachable without any privileged actor. It is explicitly acknowledged as intended behavior by the test suite comment "refund failure does not revert the outbound" [2](#0-1) , which is precisely the missing "check zero/successful balance before finalizing" gap called out in the source report.

### Likelihood Explanation
Every successful and every failed-but-reverted outbound with `gasFee > gasFeeUsed` invokes `applyGasRefund`, so the code path is hit routinely, not only in edge cases. The refund call can fail for reasons entirely outside attacker or validator control (e.g., insufficient module-held gas-token/PRC20 balance, quoter/pool issues, or EVM call failures), so triggering the failure branch does not require a malicious validator, peer, or governance action — only an unprivileged execution flow through the normal cross-chain lifecycle.

### Recommendation
Do not treat a failed `applyGasRefund` as fire-and-forget. At minimum: (1) return an error from `applyGasRefund` so callers can route failures into `AbortOutbound`, matching the pattern already used for the bridged-funds re-mint failure; and (2) do not remove the entry from `PendingOutbounds` (or add a dedicated "pending refund" index) until the refund settlement is confirmed successful, so operators have a queryable, persistent record of unresolved balance-affecting obligations instead of relying on a buried status string inside the UTX.

### Proof of Concept
1. Configure an outbound with `TxType_FUNDS` (or `GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`), `GasFee = 111`, and have validators vote `success=false` (or `true`) with `gasFeeUsed = 50`, reaching quorum via `MsgVoteOutbound`.
2. Ensure `CallUniversalCoreRefundUnusedGas` fails for both the swap and no-swap attempts (e.g., by starving the `uexecutor` module account / `UniversalCore` contract of the gas-token liquidity needed for the refund, which is a realistic operational condition rather than a contrived one).
3. Observe: `outbound.OutboundStatus` becomes `REVERTED` (or stays `OBSERVED`), `PendingOutbounds.Has(ctx, outbound.Id)` is already `false` (removed in Step 5 of `VoteOutbound`), and `PcRefundExecution.Status == "FAILED"` — the excess gas fee is permanently unaccounted for with no retriable state, as demonstrated by the existing test asserting this exact "refund failure does not revert the outbound" behavior [11](#0-10) .

### Citations

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

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/keeper/outbound.go (L149-171)
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

// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
```

**File:** x/uexecutor/keeper/outbound.go (L178-257)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
}
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L155-197)
```go
	t.Run("swap fallback reason stored when swap refund fails", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// gasFee = 111, gasFeeUsed = 1 → large excess triggers refund attempt.
		// The test handler's defaultFeeTier for an unknown gas token will either
		// return 0 or fail, causing the swap path to fall back. In either case
		// RefundSwapError must be non-empty (swap was not clean) or PcRefundExecution
		// must exist.
		gasFeeUsed := "1"

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
				true,
				"",
				gasFeeUsed,
			)
			require.NoError(t, err)
		}

		utx, _, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)

		ob := utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)

		// Refund execution must always be recorded when excess gas exists
		require.NotNil(t, ob.PcRefundExecution)

		// The outbound status stays OBSERVED (refund failure does not revert the outbound)
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)
	})
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L110-134)
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

	// Step 6: Finalize outbound (refund if failed).
	// If re-mint fails, handleFailedOutbound marks it ABORTED internally and returns nil.
	// Business logic errors are stored in RevertError on the UTX; only infra errors are returned.
	if err := k.FinalizeOutbound(ctx, utxId, outbound); err != nil {
```
