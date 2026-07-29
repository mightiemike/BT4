Confirmed: no TWAP, deviation, or reference-price validation exists anywhere in the codebase around the QuoterV2-derived swap quote. The `SqrtPriceLimitX96: big.NewInt(0)` param in `GetSwapQuote` disables any price-limit protection on the quote itself, and `minPCOut` is derived purely from that same manipulable quote.

### Title
Deposit auto-swap trusts unvalidated instantaneous Uniswap V3 spot price, enabling protocol-fund drain via same-block price manipulation - (File: x/uexecutor/keeper/evm.go)

### Summary
`GetSwapQuote()` in `x/uexecutor/keeper/evm.go` fetches a swap quote from the on-chain Uniswap V3 `QuoterV2.quoteExactInputSingle`, which reflects only the pool's current instantaneous spot price (no TWAP, no external reference/anchor price, and `SqrtPriceLimitX96` hard-coded to `0`, i.e. no price-limit bound). This quote is used both as the output amount and — self-referentially — as the basis for `minPCOut` (95% of the same quote) in `CallPRC20DepositAutoSwap` and `CallUniversalCoreRefundUnusedGas`. This is structurally the same root cause as the reported Chainlink `minAnswer` issue: a price/oracle value is trusted and used to compute both the "amount" and its own "slippage bound" without validating it against any independent, manipulation-resistant reference, so an attacker who moves the pool's spot price before this logic executes controls both numbers simultaneously.

### Finding Description
`x/uexecutor/keeper/evm.go` `GetSwapQuote()` (lines ~500-538) calls `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96: big.NewInt(0)`, meaning the quote is computed purely from whatever the pool's reserves/price happen to be at call time: [1](#0-0) 

This quote flows into `ExecuteInboundGas()` (`x/uexecutor/keeper/execute_inbound_gas.go`) and `gasAndPayloadDepositAutoSwap()` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go`), where `minPCOut` is derived from the very same `quote` value with a flat 5% haircut: [2](#0-1) 

The same pattern recurs in `applyGasRefund()` (`x/uexecutor/keeper/outbound.go`), where excess-gas refunds are swapped using an identically self-referential `minPCOut`: [3](#0-2) 

Because `minPCOut` is computed *from* the manipulated `quote` rather than from an independent oracle/TWAP/registry-configured reference price, the 95% slippage floor provides no real protection: if an attacker inflates the pool's spot price for `PRC20→WPC` immediately before their cross-chain deposit is processed (both the manipulating swap and the deposit-triggered `depositPRC20WithAutoSwap` execute against the same live pool state within the Push Chain state machine), the inflated `quote` and the inflated `minPCOut` move together. The attacker then receives an inflated amount of WPC/PC from `UniversalCore`'s liquidity for a genuine but smaller PRC20 deposit, and can subsequently reverse the pool manipulation (sell back) to recoup manipulation cost while keeping the excess. No code path in the repository checks `quote` or the resulting swap output against any chain-registry gas price, a moving-average, or a bounded deviation threshold — the analog of Chainlink's missing `minAnswer/maxAnswer` bounds check.

### Impact Explanation
This falls squarely under "corruption of PRC20 or native asset accounting" and "unauthorized ... release ... of protocol-controlled funds": the WPC/native liquidity the `UniversalCore` contract holds for auto-swaps can be drained beyond what a legitimate deposit is worth, because the only bound (`minPCOut`) is derived from the same attacker-influenced number it's supposed to protect against. This is reachable by an ordinary unprivileged user simply by (a) submitting a swap on the pool and (b) triggering a cross-chain `GAS`/`GAS_AND_PAYLOAD` deposit, both of which are default, unprivileged user actions — matching the "Universal execution path" and "Registry and accounting path" allowed-impact categories.

### Likelihood Explanation
Likelihood depends on the depth/liquidity of the specific `PRC20/WPC` Uniswap V3 pool used for that asset — for low-liquidity or newly-listed PRC20 tokens this is cheaply and reliably exploitable in a single block/transaction sequence since there is no cooldown, TWAP window, or external price cross-check anywhere in the call chain.

### Recommendation
Do not derive `minPCOut` from the same spot quote being protected against. Instead: (1) use a Uniswap V3 TWAP (time-weighted average, e.g. via `observe()`/`OracleLibrary`) rather than instantaneous `quoteExactInputSingle` for pricing, and/or (2) cross-check the quoted price against an independent reference (e.g., the validator-voted `ChainMeta`/gas-price oracle or a governance-configured acceptable price band for the PRC20/WPC pair) and revert if the deviation exceeds a configured threshold, mirroring the recommended Chainlink fix of validating the returned answer against known bounds before use.

### Proof of Concept
1. Attacker identifies a `PRC20` token registered for gasless/auto-swap deposits whose `PRC20/WPC` Uniswap V3 pool (deployed on Push Chain) has shallow liquidity.
2. In the same block/tx window, attacker performs a large swap on that pool to inflate the spot price of `PRC20` in terms of `WPC`.
3. Attacker (or a colluding relayer) submits/validators finalize a cross-chain `GAS`/`GAS_AND_PAYLOAD` inbound deposit for that `PRC20` token; `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` calls `GetSwapQuote` which returns the inflated quote, computes `minPCOut = quote*95/100`, and calls `CallPRC20DepositAutoSwap`.
4. `depositPRC20WithAutoSwap` on `UniversalCore` swaps at the manipulated pool price, releasing inflated `WPC`/native tokens to the attacker's UEA.
5. Attacker reverses the initial swap, recovering most of the manipulation capital while retaining the excess `WPC` extracted from protocol liquidity.

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

**File:** x/uexecutor/keeper/outbound.go (L213-230)
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
```
