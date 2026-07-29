## Analysis

The external report's bug class is: **a hardcoded/default risk parameter (`max_loss`) that is not validated against real conditions and cannot be adjusted, allowing unexpected loss.** The Push Chain analog is the hardcoded 5% slippage tolerance used when auto-swapping bridged PRC20 tokens for native PC during `GAS` and `GAS_AND_PAYLOAD` inbound processing, and during outbound gas-fee refunds.

### Where it lives
`x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go` (`gasAndPayloadDepositAutoSwap`), and `x/uexecutor/keeper/outbound.go` (`applyGasRefund`) all compute:

```go
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The `quote` itself comes from `GetSwapQuote`, which calls Uniswap V3 `QuoterV2.quoteExactInputSingle` — a **spot-price-based simulation**, not a TWAP: [4](#0-3) . This quote is then passed straight to `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` as `minPCOut`, so the "protection" band is only ±5% of whatever the pool's *current* (potentially manipulated) price is — it is not compared against any external/oracle reference price, and there is no governance/strategist knob to change the 5% or add circuit breakers.

### Why this matters
Because `minPCOut` is derived from the manipulable spot quote rather than a trusted reference, an attacker can push the AMM pool price down in a transaction ordered just before the block/tx that finalizes a validator-voted `GAS`/`GAS_AND_PAYLOAD` inbound (or an outbound gas refund), then reverse the trade afterward. The 5% slippage guard does not prevent this because it is computed relative to the already-degraded quote, not a fair-value reference. This is purely an unprivileged, permissionless action (ordinary AMM trades + normal tx ordering), not a validator/relayer/TSS compromise, so it fits the "corruption of PRC20 or native asset accounting" / fund-loss impact category in scope.

### Assessment
This is a plausible analog with real code to point to, but I want to flag the uncertainty honestly: whether this is *exploitable in practice* depends on details I can't fully verify from the index alone — specifically (a) the exact block/tx ordering guarantees around inbound-vote finalization (is it FIFO mempool, priority mempool, or does the proposer control ordering, which would push this toward "malicious validator" and out of scope), and (b) the actual liquidity depth assumptions for the WPC/PRC20 pools that back gas tokens (thin pools make manipulation cheaper and confirm materiality). I could not find code enforcing minimum liquidity, TWAP usage, or reference-price cross-checks anywhere in `x/uexecutor` or `uregistry`, which is what led me to flag this.

### Title
Spot-price-derived, non-adjustable 5% slippage bound on PRC20↔PC auto-swaps enables AMM price-manipulation value extraction — (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, `outbound.go`)

### Summary
`GetSwapQuote` (`x/uexecutor/keeper/evm.go`) reads the Uniswap V3 QuoterV2 spot-simulated quote and every caller (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, `applyGasRefund`) derives `minPCOut` as a flat 95% of that quote, with no reference to a trusted/TWAP price and no configurable tolerance. An unprivileged actor who moves the pool price in an adjacent transaction can cause the protocol-driven auto-swap (deposit-with-autoswap for GAS/GAS_AND_PAYLOAD inbounds, or gas-fee refund swaps for outbounds) to execute at a manipulated rate, capped only by the same manipulated 5% band, extracting value at the expense of the depositing user / protocol-held funds.

### Finding Description [4](#0-3)  shows `GetSwapQuote` performing a live `quoteExactInputSingle` call against the current AMM state (no TWAP, no staleness/liquidity checks). All three call sites [5](#0-4) [6](#0-5) [7](#0-6)  then apply a fixed `95/100` multiplier to that same manipulable quote as the on-chain slippage floor passed into `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` (`x/uexecutor/keeper/evm.go` lines 540-644). Because the bound tracks the manipulated price rather than a fair-value reference, there is no invariant that actually caps loss to 5% of true value.

### Impact Explanation
If exploitable, this results in corruption of PRC20/native asset accounting: users receiving `GAS`/`GAS_AND_PAYLOAD` inbound deposits, or senders owed excess-gas refunds on outbound observation, could receive less PC than fair value while the difference is captured by the manipulator — a form of fund loss reachable from ordinary deposit/outbound flows with honest validators and honest nodes, matching the in-scope "corruption of PRC20 or native asset accounting" / fund-loss category.

### Likelihood Explanation
Likelihood depends on details not confirmable from static reading alone: transaction-ordering guarantees at vote-finalization time, and gas-token pool liquidity depth. If ordering allows any account to place a transaction immediately before the finalizing vote/outbound-observation transaction within a block, likelihood is meaningful; if the mempool/proposer design prevents this without validator collusion, likelihood drops and the issue becomes closer to a hardening/design gap than an actively exploitable bug.

### Recommendation
Do not derive the slippage floor solely from the same instantaneous quote used for execution. Use a TWAP or externally-anchored reference price to bound acceptable swap output, add minimum-liquidity checks before allowing auto-swap, and make the slippage tolerance a governance/param-store-configurable value rather than a hardcoded `95/100` constant, consistent with the original report's recommendation to make `max_loss`-style parameters adjustable rather than fixed defaults.

### Proof of Concept
Conceptual PoC (cannot be fully substantiated without confirming block/mempool ordering behavior):
1. Attacker observes an in-flight `GAS_AND_PAYLOAD` inbound approaching 2/3 validator vote threshold, or an outbound observation about to finalize with a gas refund.
2. Attacker submits a large swap in the relevant PRC20/WPC Uniswap V3 pool ordered just before the finalizing transaction, depressing the pool's spot price.
3. The finalizing transaction triggers `GetSwapQuote` → returns the depressed quote → `minPCOut` computed as `quote*95/100` → `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes at the depressed rate, well below fair value but still "compliant" with the 5% band.
4. Attacker reverses their swap in the pool, restoring price and capturing the spread, while the deposit/refund recipient receives PC below fair value.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-148)
```go
						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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
