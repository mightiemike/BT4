### Title
Excess gas-fee refund permanently skipped when re-mint of reverted outbound funds fails - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
In `handleFailedOutbound`, when an outbound (a bridged withdrawal to an external chain) is observed to have failed and the module attempts to re-mint the bridged funds to the revert recipient via `k.CallPRC20Deposit`, a failure of that mint call causes the function to call `k.AbortOutbound` and return **immediately**, before `k.applyGasRefund` is ever invoked. This mirrors the referenced Maia finding: a fallback/error path bypasses the gas-refund step entirely, permanently stranding the excess gas fee the user already paid.

### Finding Description
`FinalizeOutbound` dispatches failed outbound observations to `handleFailedOutbound` [1](#0-0) . For funds-carrying tx types, `handleFailedOutbound` calls `k.CallPRC20Deposit` to re-mint the bridged amount back to the revert recipient. If that mint fails, the function returns via `k.AbortOutbound` without ever reaching the `k.applyGasRefund(ctx, &outbound, obs)` call that normally runs for both success and failure paths: [2](#0-1) 

Only when the re-mint succeeds does execution continue to the shared gas-refund logic: [3](#0-2) 

`applyGasRefund` is the module's only mechanism for returning the excess gas fee (`outbound.GasFee - obs.GasFeeUsed`) to the user via `UniversalCore.refundUnusedGas` [4](#0-3) . `AbortOutbound` marks the outbound as `Status_ABORTED` with an abort reason but performs no gas-refund attempt, and this status is documented as terminal, requiring manual intervention, with no automated retry/resolution path in the codebase (`ValidateBasic` treats `Status_ABORTED` as a state "set internally by AbortOutbound — no external validation needed", and no `RetryOutbound`/`ResolveAborted` handler exists) [5](#0-4) .

This is the structural analog of the `ArbitrumBranchBridgeAgent::_performFallbackCall` bug: a specific failure/fallback branch in the finalize-outbound flow (mint failure → abort) omits the refund step that every other branch (success, and failed-but-successfully-reverted) performs.

### Impact Explanation
When the bridged-funds re-mint fails (e.g., transient EVM/PRC20 failure, insufficient module gas, or any revert in `depositPRC20Token`), the outbound is aborted and the excess native/PRC20 gas fee that the user overpaid on the source chain for outbound execution (`gasFee - gasFeeUsed`) is never returned. Because `ABORTED` is treated as a terminal state requiring manual/administrative recovery, the excess gas allocation is effectively locked in `UniversalCore` and unavailable to the user through any user-reachable flow — a partial, permanent loss of user funds analogous to the original medium-severity finding (loss limited to gas refund, not full principal, since the underlying bridged amount is separately handled via `AbortOutbound`/manual intervention).

### Likelihood Explanation
This path is reachable purely through the ordinary, honest-validator outbound-voting pipeline: any outbound whose funds re-mint step fails at finalization time (network hiccups, temporary insufficient balance in `UniversalCore`, or any revert inside `CallPRC20Deposit`) triggers this code path without any privileged action, malicious validator, or malicious relayer needed — only an honest quorum voting on a failed outbound observation with a subsequently-failing re-mint call.

### Recommendation
Call `k.applyGasRefund(ctx, &outbound, obs)` before (or independent of) the `AbortOutbound` early return in `handleFailedOutbound`, so the excess gas fee is refunded regardless of whether the funds re-mint succeeds. Alternatively, persist the pending gas-refund obligation on the aborted outbound record and provide an explicit recovery/retry path (e.g., a `RetryOutboundGasRefund` message) so aborted outbounds are not a dead end for the gas-refund invariant.

### Proof of Concept
1. Reach quorum on `MsgVoteOutbound` for a `TxType_FUNDS` (or `FUNDS_AND_PAYLOAD`) outbound with `success=false` and `gas_fee_used < outbound.GasFee` (an excess-gas scenario, as exercised in `test/integration/uexecutor/gas_fee_refund_test.go`'s "failed outbound performs both revert and gas refund" case) [6](#0-5) .
2. Cause `k.CallPRC20Deposit` inside `handleFailedOutbound` to fail (e.g., simulate `UniversalCore` returning an error/revert on `depositPRC20Token`, which is plausible under low module-account balance or any EVM-level revert).
3. Observe that `handleFailedOutbound` returns via `k.AbortOutbound` at [7](#0-6)  without calling `applyGasRefund`, leaving `outbound.PcRefundExecution` nil and the excess gas fee unrefunded, with the outbound stuck in `Status_ABORTED` and no automated remediation.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L92-97)
```go
	if !obs.Success {
		return k.handleFailedOutbound(sdkCtx, utxId, outbound, obs)
	}

	return k.handleSuccessfulOutbound(sdkCtx, utxId, outbound, obs)
}
```

**File:** x/uexecutor/keeper/outbound.go (L119-147)
```go
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
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}
```

**File:** x/uexecutor/keeper/outbound.go (L149-160)
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
```

**File:** x/uexecutor/keeper/outbound.go (L174-211)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
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
```

**File:** x/uexecutor/types/outbound_tx.go (L150-152)
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
