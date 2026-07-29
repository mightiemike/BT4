### Title
Excess gas-fee refunds are silently and permanently lost on refund failure - (File: x/uexecutor/keeper/outbound.go)

### Summary
`applyGasRefund` in `x/uexecutor/keeper/outbound.go` computes excess gas fee (`gasFee - gasFeeUsed`) owed to a user after an outbound executes on a destination chain, and attempts to return it via `CallUniversalCoreRefundUnusedGas`. If both the swap-based refund and the no-swap fallback fail, the function simply records `Status = "FAILED"` on `outbound.PcRefundExecution`, logs an error, and returns — exactly the pattern in the VUSD report where a failed transfer is only logged via an event and the cycle moves on. There is no retry mechanism, no queue of failed refunds, and no message type to reprocess a `PcRefundExecution` with `Status == "FAILED"`.

### Finding Description
`applyGasRefund` [1](#0-0)  is invoked from both `handleSuccessfulOutbound` and `handleFailedOutbound` [2](#0-1)  whenever an outbound observation carries a `GasFeeUsed` lower than the reserved `GasFee`. This code path is reachable purely through the honest-validator outbound voting flow driven by ordinary user transactions (`VoteOutbound` → `FinalizeOutbound` → `handleFailedOutbound`/`handleSuccessfulOutbound` → `applyGasRefund`) [3](#0-2) .

The function first attempts a swap-based refund (gas token → PC native) and, on failure of either the quote or the swap call, falls back to a direct PRC20 deposit refund with no swap: [4](#0-3) 

If the final fallback call `CallUniversalCoreRefundUnusedGas` also errors (e.g., transient EVM failure, insufficient module balance/liquidity, or any revert in `UniversalCore.refundUnusedGas`), the code sets:
```go
refundPcTx.Status = "FAILED"
refundPcTx.ErrorMsg = err.Error()
...
outbound.PcRefundExecution = refundPcTx
outbound.RefundSwapError = swapFallbackReason
```
and returns normally — the caller (`handleFailedOutbound`/`handleSuccessfulOutbound`) then calls `k.UpdateOutbound(ctx, utxId, outbound)` to persist this terminal `FAILED` state [5](#0-4) . No subsequent code path re-attempts the refund. A repo-wide search for retry/abort message types (`MsgRetry*`, `RetryOutbound`) found none tied to `PcRefundExecution`; the only retry-adjacent flow (`ABORTED` status via `AbortOutbound`) is limited to the fund-remint failure branch of `handleFailedOutbound`, not the gas-refund branch, and even that path is documented as requiring manual/admin intervention rather than an automated or user-triggerable retry [6](#0-5) .

### Impact Explanation
The excess gas fee is protocol/user-owed value that was reserved (deducted) from the user's funds at outbound-creation time. If the on-chain refund call fails for any reason unrelated to malicious behavior (temporary AMM liquidity/slippage causing the swap-quote path to fail, and a subsequent revert in the no-swap fallback, e.g., due to PRC20 mint/transfer edge cases or module account/gas conditions), that excess amount is never returned to the user and there is no code path to retry or manually recover it for that specific outbound. This is a permanent, unrecoverable loss of user funds reachable without any privileged actor, matching the "permanent loss of protocol/user-controlled funds" allowed-impact category.

### Likelihood Explanation
This is triggered purely by the ordinary honest-validator outbound finalization flow following a normal user withdrawal/outbound where `gasFee > gasFeeUsed` (a very common real-world condition since `GasFee` is a reserved estimate and `gasFeeUsed` is the actual on-chain gas cost). The only requirement for the loss to manifest is that `CallUniversalCoreRefundUnusedGas` fails on both the swap and no-swap attempts — a condition dependent on ordinary blockchain/contract execution conditions (e.g. slippage tolerance not met, insufficient PC-side liquidity for swap, or PRC20-side edge case), not on any adversarial or privileged control. Because excess-gas-refund happens on essentially every outbound (both successful and reverted), the exposure surface is broad.

### Recommendation
On `CallUniversalCoreRefundUnusedGas` failure in `applyGasRefund`, do not treat `FAILED` as terminal. Either:
1. Queue the failed refund into a `PendingGasRefunds` (analogous to `PendingOutbounds`) collection keyed by outbound ID so it can be automatically retried in a subsequent block/tick, or
2. Add a permissionless `MsgRetryGasRefund` message that anyone (including the affected user) can submit to re-invoke `applyGasRefund` for outbounds whose `PcRefundExecution.Status == "FAILED"`, guarded by idempotency so it cannot double-refund.

### Proof of Concept
1. A user submits an inbound with a payload that results in an outbound with `GasFee = 111` and, on execution, `GasFeeUsed = 50`, leaving `refundAmount = 61` owed to the user, as exercised in `TestGasFeeRefund` [7](#0-6) .
2. Three of four validators vote to finalize the outbound observation, triggering `FinalizeOutbound → handleSuccessfulOutbound/handleFailedOutbound → applyGasRefund`.
3. Simulate `CallUniversalCoreRefundUnusedGas` failing on both the swap path (quote/swap error) and the no-swap fallback path (e.g. by having the `UniversalCore` mock/contract revert on `refundUnusedGas` for both calls).
4. Observe `outbound.PcRefundExecution.Status == "FAILED"` and `outbound.RefundSwapError` populated, matching what `TestGasFeeRefund`'s "swap fallback reason stored when swap refund fails" sub-test partially demonstrates (it only verifies the record exists, not that the 61-unit refund is ever delivered) [8](#0-7) .
5. Confirm there is no subsequent message, keeper method, or `BeginBlock`/`EndBlock` hook (the module has none — see README: "does not implement a BeginBlocker or EndBlocker") [9](#0-8)  that ever revisits this outbound's `PcRefundExecution`. The 61-unit refund is permanently lost.

### Citations

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

**File:** x/uexecutor/keeper/outbound.go (L174-256)
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

**File:** x/uexecutor/README.md (L325-327)
```markdown
## Block Lifecycle

`x/uexecutor` does not implement a `BeginBlocker` or `EndBlocker` — the module is listed in the manager's order arrays as a placeholder, but all real work happens synchronously in the message handlers. Vote tallying, inbound execution, outbound creation, and chain-meta updates are all triggered by incoming `Msg*` calls.
```
