This confirms the analog. `applyGasRefund` in `x/uexecutor/keeper/outbound.go` mirrors the `earlyEndGame` bug closely: it computes an excess-gas amount that belongs to the user/relayer, attempts to move it out of the protocol's control via `CallUniversalCoreRefundUnusedGas` (swap path, then a no-swap fallback), and if **both** attempts fail it simply records `refundPcTx.Status = "FAILED"` and `outbound.RefundSwapError` — the outbound itself is still finalized (`REVERTED`/`OBSERVED`) via `k.UpdateOutbound`, with no retry mechanism, no queued/pending-refund index, and no path for a user or admin to trigger the refund again.### Title
Excess outbound gas fee permanently stuck with no retry path when both swap and no-swap refund calls fail - ([File: x/uexecutor/keeper/outbound.go])

### Summary
When a Push Chain outbound transaction completes (successfully or with failure), `applyGasRefund` computes the excess gas fee the user overpaid (`gasFee - gasFeeUsed`) and attempts to return it via `CallUniversalCoreRefundUnusedGas`, first with a token swap, then falling back to a direct PRC20 deposit. If both attempts fail, the code merely records `PcRefundExecution.Status = "FAILED"` and `RefundSwapError`, then finalizes the outbound via `UpdateOutbound` with no mechanism to retry or otherwise route the stuck excess fee to the intended recipient — structurally the same "fee computed/deducted but never actually transferred, with no route to remediate" pattern as the reported `earlyEndGame` bug.

### Finding Description
`applyGasRefund` [1](#0-0)  computes `refundAmount := gasFee - gasFeeUsed` — funds that were already collected from the user/relayer as part of the outbound's gas budget and are rightfully owed back once actual gas usage on the destination chain is observed.

The function then tries two paths to actually move that value to the recipient:
1. Swap-based refund via `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)` [2](#0-1) 
2. No-swap fallback via `CallUniversalCoreRefundUnusedGas(..., withSwap=false, ...)` [3](#0-2) 

If both calls fail (e.g., the module's PRC20/WPC balance backing the refund pool is insufficient, `GetDefaultFeeTierForToken`/quote lookups fail, or the `UniversalCore.refundUnusedGas` EVM call reverts for any reason), the only recorded outcome is:
```go
refundPcTx.Status = "FAILED"
refundPcTx.ErrorMsg = err.Error()
...
outbound.PcRefundExecution = refundPcTx
outbound.RefundSwapError = swapFallbackReason
``` [4](#0-3) 

This is invoked unconditionally from both `handleSuccessfulOutbound` and `handleFailedOutbound`, and in both cases the function proceeds directly to `k.UpdateOutbound(ctx, utxId, outbound)`, finalizing the outbound's terminal state (`REVERTED` or `OBSERVED`) regardless of refund outcome [5](#0-4) . There is no queue, pending-refund index, or admin/permissionless message (`MsgRetryRefund`/similar) that can later re-trigger `applyGasRefund` for outbounds whose `PcRefundExecution.Status == "FAILED"`. Once the outbound is finalized, the excess gas amount is permanently unrecoverable through any user-reachable or even privileged retry flow present in this repository.

This is the same root cause as the audited `earlyEndGame` bug: a fee amount is correctly computed and semantically "owed" to a party, but the value-transfer step is fallible, and failure of that step is silently swallowed into a status field with no compensating control to actually move the value.

### Impact Explanation
The impact is a permanent loss of protocol/user-owned funds: the excess gas fee reserved at outbound creation time (already deducted from the user's asset accounting via `UniversalCore`) is neither refunded to the user nor otherwise accounted for once both refund attempts fail. Because `applyGasRefund` runs for every completed outbound (success and failure paths alike), this is not a rare edge case — any transient failure in the swap quote path, insufficient WPC liquidity, or a reverting `refundUnusedGas` call converts what should be a temporary/retryable failure into silent, permanent fund loss, matching the "Medium" impact classification of the original report (funds locked, no mechanism to recover them).

### Likelihood Explanation
Likelihood is High relative to the report's own classification pattern: the refund path depends on external, non-deterministic conditions (Uniswap-style quote availability, WPC/PRC20 liquidity in the module-controlled pool, and `UniversalCore` contract state) that are not fully within the control of validators. Any legitimate failure of the swap or no-swap call — which is plausible under normal operating conditions (e.g., liquidity pool not yet seeded for a given gas token, slippage bound violated, contract-side revert) — triggers the unrecoverable condition without requiring any privileged or malicious actor; it is reachable purely through ordinary outbound completion, which validators drive as part of default operation.

### Recommendation
Do not let a failed `PcRefundExecution` be a terminal, unrecoverable state:
- Introduce a persistent "pending refund" index (analogous to `PendingOutbounds`) keyed by outbound ID whenever `PcRefundExecution.Status == "FAILED"`, so the excess amount is tracked on-chain rather than only logged in an error string.
- Add a message (permissioned or permissionless, depending on the design) that allows retrying `applyGasRefund` for outbounds recorded with a failed refund, re-attempting swap and no-swap paths against current on-chain liquidity/state.
- Alternatively, guarantee eventual settlement by having the no-swap fallback itself be infallible for the module (e.g., minting/crediting the PRC20 directly to the recipient's balance record rather than relying on a fallible EVM call with slippage/liquidity dependencies), removing the possibility of a fully-failed refund state.

### Proof of Concept
Conceptual trigger (validated against the existing test harness in `test/integration/uexecutor/gas_fee_refund_test.go`, which already demonstrates the "PcRefundExecution.Status" being recorded as a terminal field without any subsequent retry):
1. Create an outbound with `GasFee > GasFeeUsed` (as in `TestGasFeeRefund/refund execution recorded when gasFee exceeds gasFeeUsed` [6](#0-5) ).
2. Ensure the `UniversalCore`/Uniswap-quoter mock (or in production, the real contract) fails both the swap-based `refundUnusedGas` call and the no-swap fallback call (e.g., no liquidity registered for the gas token, or the module account lacks PRC20 balance to satisfy the swap).
3. Observe that `ExecVoteOutbound` reaching quorum finalizes the outbound (`Status_OBSERVED`/`Status_REVERTED`) with `PcRefundExecution.Status == "FAILED"` and non-empty `RefundSwapError`, as directly produced by `applyGasRefund` [4](#0-3) .
4. Confirm there is no subsequent code path, message handler, or scheduled job in the repo (`grep` for `MsgRetryRefund`/`MsgRetryOutbound` returns no hits) that revisits this outbound to complete the refund — the excess gas fee is permanently stuck.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L156-172)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L174-198)
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
```

**File:** x/uexecutor/keeper/outbound.go (L213-234)
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
```

**File:** x/uexecutor/keeper/outbound.go (L239-256)
```go
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
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L108-153)
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
	})
```
