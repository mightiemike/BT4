Confirmed: there is no retry message, no admin-triggered re-refund path, and no fee-accrual fallback anywhere in `x/uexecutor` for a failed `applyGasRefund`. The result is recorded (`PcRefundExecution.Status = "FAILED"`, `RefundSwapError`) purely as telemetry — no mechanism exists to ever move the excess gas amount to the user again.

### Title
Permanent loss of user's excess-gas refund when `refundUnusedGas` fails on both swap and no-swap paths - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
`applyGasRefund` computes the excess gas fee (`gasFee - gasFeeUsed`) owed back to the outbound's sender/fund-recipient and attempts to return it via `CallUniversalCoreRefundUnusedGas`, first with an auto-swap, then falling back to a direct PRC20 deposit. If **both** attempts fail, the function only records the failure (`PcRefundExecution.Status = "FAILED"`, `RefundSwapError` set) and returns. There is no retry mechanism, no admin-triggered re-refund message, and no accrual of the un-refunded amount into any recoverable module balance — the excess gas amount becomes permanently unrecoverable by the user or the protocol.

### Finding Description
`applyGasRefund` in [1](#0-0)  is invoked from both `handleSuccessfulOutbound` and `handleFailedOutbound` [2](#0-1)  whenever an outbound observation reports `gasFeeUsed < gasFee` (i.e. the relayer used less gas than originally reserved). The refund flow is:

1. Compute `refundAmount = gasFee - gasFeeUsed`.
2. Attempt `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)` — swap the gas token back to PC and deliver it to the recipient.
3. If that fails for any reason (fee-tier lookup failure, quote failure, or the EVM call itself failing), fall back to `CallUniversalCoreRefundUnusedGas(..., withSwap=false, ...)` — a direct PRC20 deposit.
4. If the fallback **also** fails, the code simply sets `refundPcTx.Status = "FAILED"` and `refundPcTx.ErrorMsg`, attaches this to `outbound.PcRefundExecution`, and returns [3](#0-2) .

There is no code path anywhere in `x/uexecutor` that re-attempts this refund, exposes a message to retry it, or accrues the un-refunded `refundAmount` into some withdrawable/trackable balance. The value is simply gone from accounting — it is neither returned to the user nor recorded as protocol-owned fees. This mirrors the reported bug class: an amount that is computed and *should* be fully accounted for (either returned to the rightful owner or explicitly accrued) but instead only a portion of the flow is handled, and the remainder disappears from all tracked state permanently.

The test suite confirms this behavior is intentional/unaddressed as written: [4](#0-3)  only asserts that `PcRefundExecution` is *recorded*, not that the funds are ever actually delivered — the double-failure case is not covered by tests at all.

### Impact Explanation
This causes a **permanent loss of user funds** with no unprivileged or privileged path to recovery. The excess gas amount deducted/reserved upstream (deposited from the destination-chain gateway as `GasFee` on the outbound event, see `event.GasFee` in [5](#0-4) ) is meant to be returned to the sender once the actual gas usage is known. When both refund legs fail, that value is stranded — it is not credited back to the sender, not accrued to any protocol fee pool, and not retriable. This falls squarely within the "permanent freezing/permanent loss of user or protocol-controlled funds" allowed-impact category, reachable purely through ordinary user cross-chain transactions (an outbound with a gas token whose swap/deposit paths can fail, e.g. due to insufficient liquidity for the swap quote or the PRC20 deposit function reverting) and honest-validator voting (`MsgVoteOutbound`) — no privileged or malicious actor is required.

### Likelihood Explanation
Likelihood is moderate: it requires the specific combination of (a) `gasFeeUsed < gasFee` — routine, since gas estimates are conservative — and (b) both the swap-based and swap-less `refundUnusedGas` calls failing. Failure conditions for the swap path (`GetDefaultFeeTierForToken`, `getSwapQuoteForRefund`, insufficient pool liquidity, slippage-tolerance violation) are plausible in production, and the no-swap fallback can independently fail (e.g., if the gas token isn't properly registered/mintable for the recipient, or the module lacks minting rights for that PRC20 at that moment). Because this is evaluated on essentially every outbound with excess gas, over time the probability of the double-failure condition being hit at least once (for some outbound) is non-trivial, and each occurrence is an irrecoverable loss for that specific user.

### Recommendation
When both refund attempts fail, do not just log/record failure — either:
- Accrue the un-refunded `refundAmount` into a protocol-tracked, admin/governance-recoverable balance (e.g., a per-gas-token "pending refunds" ledger) so the amount can be manually reconciled and paid out later, or
- Queue the refund for automatic retry (e.g., via a pending-refunds index similar to `PendingOutbounds`) so it is reattempted in a later block once the underlying failure condition (liquidity, token registration) is resolved.

Either approach ensures the excess amount is never dropped from all trackable state, closing the parallel to the `BountyV1.sol` issue where excess funds were computed but not fully accounted for.

### Proof of Concept
1. Set up an outbound with `TxType_FUNDS` (or `GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`) whose `GasFee` (from the `UniversalTxOutbound` event) is set higher than the eventual `gasFeeUsed` reported via `MsgVoteOutbound` (see the reusable test harness `setupOutboundVotingTest` and `ExecVoteOutbound` in [6](#0-5) ).
2. Configure the gas token such that `GetDefaultFeeTierForToken`/`getSwapQuoteForRefund` fails (unregistered fee tier) **and** the fallback `CallUniversalCoreRefundUnusedGas(..., withSwap=false, ...)` also fails (e.g., PRC20 mint/deposit reverts for the recipient — for instance an unregistered or blacklisted recipient/token pairing).
3. Drive 3 UV votes to quorum for the outbound via `MsgVoteOutbound`.
4. Inspect the resulting `OutboundTx.PcRefundExecution.Status == "FAILED"` and `RefundSwapError` non-empty.
5. Observe that `refundAmount` is not present in the sender's balance, not present in any module-controlled "accrued fees" collection, and no subsequent operation in the module can re-trigger delivery of that amount — it is permanently unaccounted for.

### Citations

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

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```
