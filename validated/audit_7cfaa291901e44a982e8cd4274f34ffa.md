### Title
Slippage protection for auto-swap deposits and gas refunds is derived from the same manipulable spot AMM quote it is meant to guard against - ([File: x/uexecutor/keeper/execute_inbound_gas.go])

### Summary
The external report's root cause is a **pricing-source divergence**: a safety check (`collateralValueMinusSwapValue`) is computed with a manipulable/instant price (`tokenToEur`) instead of the manipulation-resistant average (`tokenToEurAvg`) that the rest of the invariant relies on, silently weakening the guard the check is supposed to provide. The equivalent invariant-weakening pattern exists in Push Chain's inbound gas-abstraction auto-swap and gas-refund paths: the `minPCOut` slippage floor is derived from the exact same instantaneous AMM spot quote that will be used to execute the swap, rather than from an independent, manipulation-resistant reference price. This makes the "protection" self-referential and defeats its purpose against pool-price manipulation timed around inbound finalization.

### Finding Description
`ExecuteInboundGas` (triggered automatically once a UV ballot finalizes an inbound "GAS" deposit) fetches a live Uniswap V3 `QuoterV2.quoteExactInputSingle` quote via `k.GetSwapQuote`, then computes the swap's minimum-output guard directly from that same quote: [1](#0-0) 

```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

The same pattern is repeated verbatim in the GAS_AND_PAYLOAD auto-swap helper: [2](#0-1) 

and in the outbound gas-refund path, where the excess gas is swapped back to native PC and the same 95%-of-spot-quote formula is used as the floor: [3](#0-2) 

`GetSwapQuote` itself is a static, same-block call into the live pool state (`CallEVM` with `commit=false`), reflecting whatever the pool's reserves are at the moment the deterministic state transition executes: [4](#0-3) 

Because `minPCOut` is computed from the *same* pool state that will immediately execute the swap, an attacker who skews the WPC/PRC20 pool (e.g., via a large ordinary swap on the pool contract) just before the block in which the deposit-triggered auto-swap or gas-refund auto-swap executes can push the spot price in their favor. The 5% band computed off that already-skewed price still "passes" the swap, because the floor moves together with the manipulated quote — it never references an independent, time-weighted or oracle-anchored price the way `tokenToEurAvg()` was meant to do in the original report. The victim (the depositing user's bridged funds, or the protocol's refunded gas) receives less real value than it should, and the attacker captures the difference by trading back against the pool afterward (classic sandwich pattern), all while the on-chain slippage check reports success.

### Impact Explanation
This falls under "stealing... of user or protocol-controlled funds" from the allowed-impact gate: an unprivileged attacker manipulating the AMM pool around inbound finalization can extract value from a user's cross-chain deposit conversion (`depositPRC20WithAutoSwap`) or from the protocol's gas refund (`refundUnusedGas` with `withSwap=true`), since the guard rail is derived from the same manipulable spot price it should protect against.

### Likelihood Explanation
Exploitation requires only ordinary, unprivileged transactions against the on-chain Uniswap pool timed near the block where a pending inbound vote finalizes (execution timing is externally observable via mempool/vote-tally state), no validator or TSS collusion is needed. Actual profitability depends on pool depth/liquidity relative to the swapped amount and the fixed 5% band, which bounds but does not eliminate the extractable value.

### Recommendation
Bound `minPCOut` (and the analogous refund-swap floor) using an independent, manipulation-resistant reference price — e.g., a TWAP from the pool, or the chain's already-existing gas-price/chain-meta oracle converted through a stable conversion path — rather than deriving the slippage floor from the same instantaneous `quoteExactInputSingle` call that will execute the trade.

### Proof of Concept
1. Attacker observes a pending inbound "GAS" deposit whose UV ballot is about to reach 2/3 finalization (visible via mempool `MsgVoteInbound` messages or `PendingInbounds` state).
2. Immediately before/alongside the finalizing vote lands in a block, the attacker submits an ordinary large swap against the WPC/PRC20 pool used by `GetSwapQuote`, skewing the pool price.
3. When `ExecuteInboundGas` runs in the same or next block, `GetSwapQuote` returns the skewed price; `minPCOut = quote * 95/100` is computed from that skewed value, so `CallPRC20DepositAutoSwap` still succeeds even though the executed price is worse than fair value.
4. The attacker reverses their position against the pool afterward, capturing the spread that was extracted from the deposit's auto-swap (or, analogously, from a gas refund's auto-swap in `applyGasRefund`).

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
```go
	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L213-223)
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
