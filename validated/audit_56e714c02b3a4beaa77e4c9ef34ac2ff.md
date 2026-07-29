## Title
No External Sanity Bound on Manipulable Uniswap V3 Spot-Price Used for `minPCOut` in Deposit-Auto-Swap and Gas-Refund Swaps - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report flags that LST price oracles are trusted with no upper/lower bound, no fallback, and no protection from rate manipulation before being used for fund-affecting math. Push Chain's `GAS`/`GAS_AND_PAYLOAD` inbound execution path and the outbound gas-refund path have the same structural weakness: they fetch a live, single-block Uniswap V3 `QuoterV2` spot-price quote and derive their only "slippage protection" (`minPCOut`) from that same manipulable quote, with no TWAP, no external reference price, and no bound check.

### Finding Description
`Keeper.GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` to obtain the instantaneous swap-out amount for `prc20 → WPC` at the pool's current spot price: [1](#0-0) 

This quote is the sole input used to compute `minPCOut` as a flat 95% of the quote, both when auto-swapping a `GAS`/`GAS_AND_PAYLOAD` inbound deposit: [2](#0-1) [3](#0-2) 

and when refunding unused outbound gas fees: [4](#0-3) 

The `minPCOut` value is not compared against any independently sourced reference (e.g., the `ChainMeta`/oracle-derived price, a TWAP, or a hard-coded bound). Because the "protection" (5% band) is computed from the very same spot price it is meant to protect against, an attacker who can move the Push Chain-hosted Uniswap V3 pool's spot price for the relevant `PRC20/WPC` pair before the quote is fetched can force `minPCOut` down to match a manipulated price, then let the swap execute at that manipulated (unfavorable) price — capturing the difference once the pool reverts to its true price. This is exactly the risk class in the report: "fully trusting `getRate()`... significantly increases attack surface" — here, fully trusting the AMM's live spot quote as its own slippage bound.

### Impact Explanation
This affects `depositPRC20WithAutoSwap` calls that move real user deposit value (`GAS`/`GAS_AND_PAYLOAD` inbound routes) and `refundUnusedGas` swap calls that move protocol-refunded gas value back to users. A successful price-manipulation/sandwich around these swaps causes the affected user (depositor or gas-refund recipient) to receive less PC-native value than fair market rate for their PRC20, with the difference extracted by the attacker manipulating the pool — a fund-loss impact on user-controlled/protocol-mediated value, matching "stealing... of user or protocol-controlled funds" in the allowed impact list.

### Likelihood Explanation
Reachability requires only an unprivileged attacker able to submit ordinary EVM transactions on Push Chain against the relevant Uniswap V3 pool and to time them relative to when honest validators' `MsgVoteInbound`/outbound-vote finalization executes the auto-swap or refund (both are triggered automatically, without further attacker interaction, once honest validators observe the deposit/outbound). No validator, TSS, or admin compromise is needed — the weakness is purely in scoped code trusting an unbounded, unauthenticated-from-fund-safety-perspective on-chain spot price.

### Recommendation
Do not derive `minPCOut`'s safety margin solely from the instantaneous `QuoterV2` quote. Compare the quote against an independent reference (e.g., the `ChainMeta`/oracle price, a stored/TWAP-based reference price for the pair, or a maximum allowed deviation from a recent moving average) before using it, and reject or fall back (no-swap path already exists) when the quote deviates beyond a safe band. Consider sourcing a TWAP from the pool itself (`OBSERVE`) rather than a single-block spot quote.

### Proof of Concept
1. Attacker observes an in-flight cross-chain deposit that will trigger a `GAS`/`GAS_AND_PAYLOAD` inbound (or an outbound about to be finalized with excess gas to refund) for token `PRC20_X`.
2. Attacker submits an ordinary Push Chain EVM transaction that swaps a large amount through the `PRC20_X/WPC` Uniswap V3 pool used by `GetSwapQuote`, moving its spot price against `PRC20_X`.
3. Before the pool price reverts, the honest validators' finalizing transaction triggers `gasAndPayloadDepositAutoSwap`/`applyGasRefund`, which calls `GetSwapQuote` and computes `minPCOut = quote * 95/100` off the manipulated (lowered) price.
4. `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap against the still-depressed pool, and the user's PRC20 is converted to PC-native at the manipulated, unfavorable rate, with `minPCOut` offering no real protection since it was computed from the same manipulated quote.
5. Attacker reverses their initial swap, restoring the pool price and capturing the value difference.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-379)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
}
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
