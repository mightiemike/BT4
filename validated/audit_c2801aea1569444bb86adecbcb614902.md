Confirmed: no retry/sweep function exists for a failed gas refund (`RetryRefund`, `SweepRefund`, etc. are absent from `x/uexecutor`), and the only related recovery path (`AbortOutbound`) is used solely for failed re-mint of bridged principal, not for the gas-refund leg.

### Title
Permanently unclaimable excess gas-fee reserve when both refund legs of `applyGasRefund` fail - (File: x/uexecutor/keeper/outbound.go)

### Summary
`applyGasRefund` computes `gasFee - gasFeeUsed` (the portion of the pre-reserved relayer gas fee that was not actually consumed on the destination chain) and attempts to return it to the user via `CallUniversalCoreRefundUnusedGas`, first with a swap leg and then with a raw-deposit fallback. If both legs fail, the function simply records `PcRefundExecution.Status = "FAILED"` and returns — there is no retry mechanism, no admin sweep, and the outbound's `OutboundStatus` still finalizes to `OBSERVED`/`REVERTED`. This is the same bug class as the ZeroLend M-10 finding: a value that was legitimately taken from a user (the reserved `gasFee`) exceeds the value legitimately owed out (`gasFeeUsed` to the relayer, plus whatever refund succeeds), and the surplus has no mechanism to ever be reclaimed.

### Finding Description
`outbound.GasFee` is fixed at outbound-creation time from the `UniversalTxOutbound` gateway event [1](#0-0) , representing the amount of `gasToken` already reserved/charged from the user's flow to pay the relayer. Later, once 2/3+ Universal Validators vote the destination-chain observation (`gas_fee_used`), `VoteOutbound` finalizes the outbound exactly once — guarded by `outbound.OutboundStatus != types.Status_PENDING` — and calls `handleSuccessfulOutbound`/`handleFailedOutbound`, both of which call `applyGasRefund` [2](#0-1) [3](#0-2) .

`applyGasRefund` computes `refundAmount = gasFee - gasFeeUsed` and tries two independent EVM calls to `UniversalCore.refundUnusedGas`: a swap-based path (`gasToken → PC`) and, on any failure (fee-tier lookup, quote fetch, or the swap call itself), a no-swap fallback that deposits the raw PRC20 directly [4](#0-3) . If the fallback call also fails (e.g., insufficient PRC20 in the module/contract balance to satisfy `refundUnusedGas`, `minPCOut`/slippage violation on the underlying router at that specific call, or any other reason `DerivedEVMCall` returns an error), the code only sets `refundPcTx.Status = "FAILED"` and stores it as `outbound.PcRefundExecution` [5](#0-4) . Because `VoteOutbound` finalizes each outbound exactly once (the `Status_PENDING` check rejects any further votes on the same `outboundId`) [2](#0-1) , `applyGasRefund` can never be invoked again for that outbound. There is no message, keeper function, or module hook anywhere in `x/uexecutor` that retries or sweeps a `FAILED` `PcRefundExecution` — the only comparable recovery path, `AbortOutbound`, is reserved for a failed re-mint of the bridged principal, not for the gas-refund leg [6](#0-5) .

The `refundAmount` of `gasToken` (PRC20) therefore remains permanently held by the system with no owner and no claim path — precisely the "unclaimable reserve asset buildup" pattern from the source report: a legitimately-collected value (`gasFee`) minus a legitimately-spent value (`gasFeeUsed`) leaves a residual that the protocol intends to return but has no fallback for when the return mechanism itself fails.

### Impact Explanation
Every ordinary cross-chain outbound (FUNDS, GAS_AND_PAYLOAD, FUNDS_AND_PAYLOAD, INBOUND_REVERT, RESCUE_FUNDS) reserves a `gasFee` and is subject to this refund step regardless of outcome — it runs on both `handleSuccessfulOutbound` and `handleFailedOutbound` [7](#0-6) . Any transient condition causing both the swap-refund and the no-swap fallback to fail (e.g., temporary illiquidity of the destination-chain PRC20/native pool used for the swap quote, or the receiving PRC20 hitting a supply/liquidity cap on direct deposit) permanently locks the `refundAmount` in the contract, with no recorded owner and no way for the affected user (or anyone) to reclaim it. Over many outbounds this surplus accumulates without bound, matching the Medium-severity precedent set in the referenced Sherlock judgment (funds becoming permanently and unconditionally unretrievable, exceeding the 0.01%/$10 threshold as volume grows).

### Likelihood Explanation
No privileged or malicious actor is required — honest Universal Validators correctly report `gas_fee_used`, and the loss condition is triggered purely by ordinary EVM-call failure modes inside `refundUnusedGas` (both the swap and non-swap variants) at the moment finalization runs. Because finalization happens exactly once per outbound with no retry, any single transient failure of both refund legs is sufficient and irreversible.

### Recommendation
Add an idempotent retry/sweep path for outbounds whose `PcRefundExecution.Status == "FAILED"` — e.g., a keeper function (invokable via a scheduled job, `EndBlocker`, or a permissionless message) that re-attempts `CallUniversalCoreRefundUnusedGas` for previously failed refunds, or an explicit accounting record (`UnclaimedRefund`) that a user can later claim once conditions normalize, instead of silently discarding the failure after a single attempt.

### Proof of Concept
1. A user's outbound (any funds-bearing `TxType`) is created with `GasFee = X` (from `BuildOutboundsFromReceipt`).
2. Universal Validators observe and honestly vote `gas_fee_used = Y` where `Y < X`, and the ballot finalizes, calling `handleSuccessfulOutbound`/`handleFailedOutbound` → `applyGasRefund`.
3. At execution time, the `UniversalCore.refundUnusedGas(gasToken, X-Y, recipient, true, fee, minPCOut)` swap-leg call reverts (e.g., because destination-chain price moved and slippage now violates `minPCOut`), and the fallback `refundUnusedGas(gasToken, X-Y, recipient, false, 0, 0)` call also reverts (e.g., PRC20 mint/deposit cap reached).
4. `applyGasRefund` records `PcRefundExecution.Status = "FAILED"` and returns; `UpdateOutbound` persists the outbound with `OutboundStatus = OBSERVED`/`REVERTED`.
5. Because `VoteOutbound` rejects any further vote once `OutboundStatus != PENDING`, this outbound's refund can never be retried, and the `X-Y` worth of `gasToken` is permanently stranded with no code path to reclaim it.

### Citations

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

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L62-69)
```go
	// Prevent double-finalization
	if outbound.OutboundStatus != types.Status_PENDING {
		k.Logger().Warn("vote outbound rejected: outbound already finalized",
			"outbound_id", outboundId,
			"status", outbound.OutboundStatus.String(),
		)
		return fmt.Errorf("outbound with key %s is already finalized", outboundId)
	}
```

**File:** x/uexecutor/keeper/outbound.go (L130-136)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
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

**File:** x/uexecutor/keeper/outbound.go (L213-256)
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
```
