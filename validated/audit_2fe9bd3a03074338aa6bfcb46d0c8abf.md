### Title
Gas-refund swaps use an unprotected spot AMM quote, letting an attacker manipulate the price to over-extract PC on `refundUnusedGas` - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
The Ajna bug is a self-dealing attack: the attacker manipulates an internal price/threshold value (LUP) that gates how much value can be pulled out of a position, drains the resulting artificial bad debt from protocol reserves, and repeats the cycle for profit. The closest reachable analog in Push Chain's scoped `x/uexecutor` code is the unused-gas refund path, where the amount of native `PC` paid out to a user is derived from a **spot** Uniswap-V3-style quote (`GetSwapQuote`/`quoteExactInputSingle`) fetched at the moment of refund, with slippage protection (`minPCOut`) computed from that *same* manipulable quote rather than an independent reference price.

### Finding Description
`applyGasRefund` in [1](#0-0)  computes the excess gas (`gasFee - gasFeeUsed`) and, to refund it in native PC, fetches a swap quote via `getSwapQuoteForRefund` → `GetSwapQuote`, which calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` view function directly against live pool state [2](#0-1) .

The resulting `quote` is then used to compute the *only* slippage bound applied to the actual swap:

```go
quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
``` [3](#0-2) 

Because `minPCOut` is derived from the *same* spot price that the swap itself will execute against (no TWAP, no external oracle, no separate reference), this only bounds additional slippage that occurs *within* the swap call — it provides no protection against the pool's spot price having already been pushed away from fair value by the attacker beforehand. An unprivileged actor who is the `outbound.Sender` (or controls `RevertInstructions.FundRecipient`) can:
1. Skew the WPC/PRC20 pool price in their favor via ordinary swaps (unprivileged, permissionless AMM interaction).
2. Trigger or wait for their own outbound's `applyGasRefund` path to run (fires automatically once UVs vote `MsgVoteOutbound`, for both success — `handleSuccessfulOutbound` — and failure — `handleFailedOutbound` — cases: [4](#0-3) ).
3. Receive an inflated amount of native PC out of the pool for a fixed `refundAmount` of gas token, because the quote (and therefore the accepted `minPCOut`) reflects the manipulated price, not the pre-manipulation fair price.
4. Revert the price skew (or let it be arbitraged back), pocketing the difference, and repeat with subsequent outbounds/refunds.

This mirrors the Ajna pattern structurally: an unprivileged, self-controlled sequence of transactions manipulates an internally-computed price/threshold that gates how much value is released, and the excess is paid out of a shared pool of value (here, the WPC/PRC20 AMM pool feeding `refundUnusedGas`) rather than directly from the victim's own funds.

### Impact Explanation
If exploitable, this would let an attacker extract more native PC per refund than the fair-value excess gas fee actually owed, draining the AMM pool (and, by extension, whatever liquidity/reserves back it) over repeated outbound cycles. This falls under "corruption of ... gas fee accounting, refund accounting" and "unauthorized ... release ... of user or protocol-controlled funds" in the allowed impact set.

### Likelihood Explanation
Likelihood is **low-to-moderate** and requires real capital and timing risk analogous to the Ajna case: the attacker must move the pool price, cannot fully control exactly when the validator-driven `MsgVoteOutbound` (and thus the refund) lands, and bears arbitrage/slippage risk plus the 5%-vs-spot bound narrowing the extractable margin. It also depends on the actual liquidity depth of the specific WPC/PRC20 pool, which is external, deployed contract state not fully visible in this repo. This is a plausible but speculative analog; I could not find in-scope logic that ties `minPCOut`/quote to any TWAP or independent reference price, which is the concrete gap enabling the manipulation.

### Recommendation
Base `minPCOut` on a time-weighted average price (or an independent, harder-to-manipulate oracle) rather than the same spot quote used to execute the swap, and/or cap per-block/per-tx refund-swap notional, similar in spirit to the original recommendation of adding a buffer independent of the manipulable value used to gate fund release.

### Proof of Concept
Not runnable from this analysis — validating this would require deploying the actual `UniversalCore`/`QuoterV2`/pool contracts (out of this repo's indexed scope) and simulating: (1) an outbound with `GasFee` sender-controlled, (2) a pool-price skew via ordinary swaps immediately before `MsgVoteOutbound` finalizes, and (3) measuring the PC payout via `CallUniversalCoreRefundUnusedGas` against the pre-skew fair price to confirm extractable excess exceeds slippage/arbitrage cost.

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

**File:** x/uexecutor/keeper/outbound.go (L174-237)
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
```

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
}
```
