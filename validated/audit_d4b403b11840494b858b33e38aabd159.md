## Analysis: Gas Refund Failure Leaves Excess Gas Permanently Stuck With No Retry Path

The IONIC bug's core pattern — a value is computed/recorded during a cross-chain-adjacent flow, but the code path to actually deliver it to the user is missing or can silently fail with no recovery mechanism — has a direct analog in Push Chain's outbound gas-refund logic.

### Title
Excess outbound gas refund can permanently strand user funds in UniversalCore with no retry/rescue path - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
When an outbound finishes (success or failure), `applyGasRefund` computes the excess gas fee (`gasFee - gasFeeUsed`) and attempts to return it to the user via `CallUniversalCoreRefundUnusedGas`, first with a DEX swap back to native PC and then via a no-swap fallback. [1](#0-0)  If both attempts fail, the code merely records `Status: "FAILED"` on `outbound.PcRefundExecution` and continues — unlike the sibling bridged-funds-revert failure path in `handleFailedOutbound`, which explicitly calls `k.AbortOutbound` to flag the outbound for manual intervention. [2](#0-1) 

### Finding Description
`applyGasRefund` is invoked from both `handleSuccessfulOutbound` and `handleFailedOutbound` for every outbound where excess gas exists. [3](#0-2)  On failure of both the swap and no-swap refund calls, `refundPcTx.Status = "FAILED"` is set and stored, and `k.UpdateOutbound` is called to persist the outbound in its normal `REVERTED`/`OBSERVED` terminal state — there is no `AbortOutbound` call, no distinct alerting event, and no queued retry mechanism. [4](#0-3)  A grep across the codebase for any retry/rescue mechanism keyed to a failed `PcRefundExecution` (e.g., `RetryGasRefund`) found none — the only comparable recovery path (`RescueFundsOnSourceChain` / `AttachRescueOutboundFromReceipt`) is scoped to CEA-deposit failures and reverted `INBOUND_REVERT` outbounds, not to failed gas refunds. [5](#0-4) 

Once this happens, the excess gas amount is held inside the `UniversalCore` EVM contract (the module-managed handler contract) but no code path in `x/uexecutor` ever re-attempts or exposes a way to claim it. This mirrors the IONIC report exactly: a value is computed and recorded (`interestFromExternalProtocolDuringLiquidation` / `PcRefundExecution.Status = "FAILED"`), but the withdrawal mechanism is either non-existent or silently abandoned.

### Impact Explanation
Any time the refund call fails (e.g., transient EVM/module-account nonce issue, swap quoter/fee-tier failure, or a revert inside `refundUnusedGas`), the user's excess-gas portion is permanently unrecoverable through any on-chain flow — a **permanent loss of user funds**, which is explicitly in the allowed-impact list.

### Likelihood Explanation
This does not require malicious validator or privileged behavior — it is triggered purely by ordinary outbound execution combined with any failure in the two-step refund attempt (swap-fee-tier lookup, quote fetch, or the EVM call itself), all of which are best-effort external calls that can revert for legitimate reasons unrelated to attacker action, making this a foreseeable production condition rather than an edge case requiring a malicious actor.

### Recommendation
Treat a failed `PcRefundExecution` the same way a failed bridged-funds revert is treated: call `k.AbortOutbound` (or an equivalent) to flag it for manual/governance-driven recovery, and/or add a dedicated retry message (analogous to the rescue-funds flow) that lets governance or the affected user re-trigger `CallUniversalCoreRefundUnusedGas` for outbounds whose `PcRefundExecution.Status == "FAILED"`.

### Proof of Concept
1. Vote an outbound to `OBSERVED`/failed with `GasFeeUsed < GasFee` so `applyGasRefund` computes a positive `refundAmount`. [6](#0-5) 
2. Force both the swap path and no-swap fallback to fail (e.g., by having `GetDefaultFeeTierForToken`/quote lookup fail and having `CallUniversalCoreRefundUnusedGas` with `withSwap=false` also revert).
3. Observe `outbound.PcRefundExecution.Status == "FAILED"` is persisted via `k.UpdateOutbound`, with no `AbortOutbound` call and no other on-chain hook to retry — the outbound remains in its normal terminal state, and the excess gas is unreachable through any subsequent message in the module.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L130-141)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L163-172)
```go
// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-257)
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

**File:** x/uexecutor/keeper/create_outbound.go (L239-262)
```go
		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
		} else {
			hasRevertedAutoRevert := false
			for _, ob := range originalUtx.OutboundTx {
				if ob != nil && ob.TxType == types.TxType_INBOUND_REVERT && ob.OutboundStatus == types.Status_REVERTED {
					hasRevertedAutoRevert = true
					break
				}
			}
			if !hasRevertedAutoRevert {
				return fmt.Errorf("rescue: UTX %s has no reverted inbound-revert outbound", originalUtxId)
			}
		}
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L108-152)
```go
	t.Run("refund execution recorded when gasFee exceeds gasFeeUsed", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// gasFee = 111 (set by mock), gasFeeUsed = 50 → 61 excess to refund
		gasFeeUsed := "50"

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

		fmt.Println(utx)

		ob := utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)
		require.True(t, ob.ObservedTx.Success)

		// Refund was attempted → PcRefundExecution must be set
		require.NotNil(t, ob.PcRefundExecution,
			"PcRefundExecution must be set when excess gas fee exists")

		// In the test environment the UniversalCore stub may or may not implement
		// refundUnusedGas. The important invariant is that the execution record
		// is stored regardless of EVM success/failure.
		require.NotEmpty(t, ob.PcRefundExecution.Status,
			"PcRefundExecution.Status must be set")
```
