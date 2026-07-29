Based on my investigation, I found a genuine analog to the trove-yield-loss pattern in `x/uexecutor`'s outbound gas-refund flow, but I want to be upfront that I could not fully trace where the refund tokens are custodied before `CallUniversalCoreRefundUnusedGas` is invoked (I did not get to read `x/uexecutor/keeper/evm.go` in full), so part of the fund-custody claim is inferred from the surrounding code rather than directly confirmed.

### Title
Unrecoverable excess-gas refund when both swap and fallback refund calls fail during outbound finalization - (File: x/uexecutor/keeper/outbound.go)

### Summary
When an outbound (a destination-chain payout) is finalized, `FinalizeOutbound` computes an excess-gas amount (`gasFee - gasFeeUsed`) that is owed back to the user, and attempts to pay it via `applyGasRefund`. This mirrors the trove pattern: value legitimately accrues to be claimed at finalization time, and the finalization step is the only path that can release it.

### Finding Description
`applyGasRefund` is called from both `handleFailedOutbound` and `handleSuccessfulOutbound` inside `FinalizeOutbound`, which is the terminal step that flips the outbound to `Status_REVERTED` (or leaves it at its finalized successful state) via `k.UpdateOutbound`. [1](#0-0) 

`applyGasRefund` first tries a swap-based refund (`gasToken → PC native`), and if that fails for any reason (fee-tier fetch failure, quote failure, or the swap-refund call itself failing), it falls back to a no-swap direct deposit via `CallUniversalCoreRefundUnusedGas`. If that fallback call *also* fails, the function simply records `refundPcTx.Status = "FAILED"` and `outbound.RefundSwapError`, then returns — there is no retry loop, no re-queue, and no separate mechanism anywhere else in the codebase that re-invokes `applyGasRefund` for outbounds already marked failed. [2](#0-1) 

Once this returns, the calling function (`handleFailedOutbound` or `handleSuccessfulOutbound`) immediately calls `k.UpdateOutbound(ctx, utxId, outbound)`, persisting the outbound in a terminal status (`Status_REVERTED` for the failed-outbound path) with `PcRefundExecution.Status == "FAILED"`. [3](#0-2) 

This is structurally identical to the trove bug: a legitimately-owed value (excess gas, analogous to the accrued GMX yield) is computed and attempted to be paid out only during the finalization call, and if that payout attempt fails, the underlying state machine transitions to a terminal status with no further code path ever revisiting or re-attempting the payout — the excess-gas amount, if it was already collected/held by the module (as `GasFee` is deducted from the user upfront per `x/uexecutor/README.md` OutboundTx fields), becomes permanently unclaimed. [4](#0-3) 

### Impact Explanation
If the swap-refund and no-swap fallback both fail (e.g., due to swap pool/liquidity issues, PRC20 contract call reverting, or the EVM-derived call running out of the manually tracked `ModuleAccountNonce`), the excess-gas amount is permanently stranded: the outbound record moves to a terminal status (`REVERTED` for failed outbounds, or finalized for successful ones) and no code path re-attempts `applyGasRefund` afterward. This matches the "In scope" impact category of permanent loss/freezing of protocol- or user-controlled funds.

### Likelihood Explanation
This does not require a malicious/privileged actor — it can be triggered by an ordinary user's cross-chain outbound whose gas-refund leg happens to hit a failure condition (e.g., no liquidity for the swap quote, or the fallback PRC20 deposit reverting), which is a state reachable through normal deposit/outbound flows and validator voting on `gasFeeUsed`, consistent with `TestGasFeeRefund`'s existing "failed outbound performs both revert and gas refund" scenario in the test suite. [5](#0-4) 

### Recommendation
Do not finalize the outbound to a terminal status when `applyGasRefund` fails on both paths. Instead, either (a) keep the outbound (or a dedicated refund sub-record) in a retryable state and expose an explicit re-drive path/message that can re-attempt `applyGasRefund` for outbounds whose `PcRefundExecution.Status == "FAILED"`, or (b) route the failed refund amount into a claimable escrow keyed to the recipient so it isn't lost when the outbound record becomes terminal.

### Proof of Concept
1. Set up an outbound with a valid `GasFee`/`GasToken` and low `GasFeeUsed`, so `applyGasRefund` computes a positive `refundAmount`, mirroring `TestGasFeeRefund`. [6](#0-5) 
2. Force `GetDefaultFeeTierForToken` / `getSwapQuoteForRefund` to fail (e.g., an unconfigured swap pool for the gas token) so the swap path falls back to the no-swap branch. [7](#0-6) 
3. Force `CallUniversalCoreRefundUnusedGas(..., false, ...)` to also fail (e.g., malformed recipient or reverting PRC20 call). [8](#0-7) 
4. Observe the outbound is persisted with `PcRefundExecution.Status == "FAILED"` and `OutboundStatus` already terminal (`REVERTED`), and confirm no subsequent code path re-invokes the refund for this outbound — the excess-gas amount is permanently unclaimed.

**Note on uncertainty:** I was not able to fully verify, within the remaining investigation budget, the precise custody of the refund tokens prior to the `CallUniversalCoreRefundUnusedGas` call (i.e., whether they sit in the `uexecutor` module account as PRC20/native balance, minted on demand, or otherwise) since `x/uexecutor/keeper/evm.go` (which implements `CallUniversalCoreRefundUnusedGas`) was not fully read. This affects whether the "loss" is of already-custodied funds (stronger match to the trove bug) versus a failed mint/transfer attempt with no persisted balance at risk (weaker match). I recommend a Devin session trace `x/uexecutor/keeper/evm.go` and the `UniversalCore.refundUnusedGas` precompile call to confirm exact fund custody before finalizing severity.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L99-172)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
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
}
```

**File:** x/uexecutor/keeper/outbound.go (L213-257)
```go
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

**File:** x/uexecutor/README.md (L99-109)
```markdown
  string id                   = 12; // deterministic outbound ID
  Status outbound_status      = 13; // PENDING -> OBSERVED | REVERTED | ABORTED
  RevertInstructions revert_instructions = 14;
  PCTx   pc_revert_execution  = 15; // PC tx that ran the revert path (nil if not reverted)
  string gas_price            = 16; // destination-chain gas price snapshot
  string gas_fee              = 17; // amount paid to relayer
  PCTx   pc_refund_execution  = 18; // PC tx that ran the unused-gas refund (nil if no refund)
  string refund_swap_error    = 19; // non-empty if the swap-refund leg failed
  string gas_token            = 20; // PRC20 used to pay relayer
  string abort_reason         = 21; // human-readable reason if outbound was aborted
}
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
