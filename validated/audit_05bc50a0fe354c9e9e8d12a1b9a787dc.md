### Title
GAS / GAS_AND_PAYLOAD auto-swap uses an unprotected spot-price Uniswap V3 quote, letting an unprivileged user manipulate `minPCOut` and extract value from the PRC20↔WPC pool - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The external report's root cause is a price-oracle class bug: a protocol relies on a spot AMM price (Uniswap) as its only source of truth for an asset's value, and that AMM's low liquidity makes the price trivially manipulable, breaking correctness guarantees for anyone who depends on it. Push Chain has a direct structural analog in its own native gas-abstraction swap path: instead of a lending protocol trusting a bad price feed for collateral, `x/uexecutor` trusts a single, same-block Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote to compute the minimum acceptable output (`minPCOut`) for every PRC20→WPC auto-swap executed as part of processing a `GAS` or `GAS_AND_PAYLOAD` inbound.

### Finding Description
For `GAS` and `GAS_AND_PAYLOAD` inbound transaction types, `ExecuteInboundGas` and `ExecuteInboundGasAndPayload` (via `gasAndPayloadDepositAutoSwap`) compute a slippage floor purely from a live on-chain quote and then immediately execute the swap against the same pool: [1](#0-0) 

```
fee   = GetDefaultFeeTierForToken(prc20)
quote = GetSwapQuote(quoterAddr, prc20, wpc, fee, amount)   // QuoterV2.quoteExactInputSingle spot read
minPCOut = quote * 95 / 100                                  // fixed 5% slippage tolerance
CallPRC20DepositAutoSwap(prc20, uea, amount, fee, minPCOut)  // actual swap against the same pool
```

`GetSwapQuote` calls `quoteExactInputSingle` directly on the live Uniswap V3 `QuoterV2` contract with no TWAP, no external price cross-check, and no minimum-liquidity/deviation guard: [2](#0-1) 

The same unprotected pattern is reused for the outbound gas-refund swap in `applyGasRefund` / `getSwapQuoteForRefund`: [3](#0-2) [4](#0-3) 

This mirrors the Blueberry report's exact concern — "Uniswap oracles ... highly dangerous given their low liquidity" — except here the AMM pool (PRC20↔WPC on Push Chain's own EVM, per the QuoterV2/SwapRouter deployment referenced in `e2e-tests/setup.sh`) is a brand-new, protocol-created pool that is very likely to have thin liquidity, especially for long-tail gas tokens whitelisted via `uregistry` (any admin-whitelisted `TokenConfig`, e.g. `steth.json`, can be routed through this swap path once it has a PRC20 native representation).

Because the quote and the swap execution happen back-to-back in the same keeper call (not truly atomic with an external actor's transaction, but both reference identical pool state at the time of execution), any unprivileged user who can move the PRC20/WPC pool price in advance — via ordinary swaps on the pool through normal EVM transactions on Push Chain, which is a permissionless chain where anyone can submit txs — can bias the price *before* their own inbound-triggered auto-swap is processed by the `uexecutor` module. The 5% band around a self-manipulated spot price is not a meaningful protection: the attacker sets the "true" reference price themselves, so the swap always clears at whatever manipulated ratio they arranged, letting them extract WPC (backed by protocol/user funds) from the pool at a favorable rate, or conversely force other users' concurrent gas-swaps to execute at their manipulated unfavorable rate, corrupting native/PRC20 asset accounting during the deposit path (`depositPRC20` in `x/uexecutor/keeper/handler.go`) and the gas-refund path.

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting ... reachable from ordinary user deposits" and potentially "unauthorized mint ... of user or protocol-controlled funds," both explicitly in scope. An attacker who can cheaply move the price in a low-liquidity PRC20/WPC pool can systematically extract value from every `GAS`/`GAS_AND_PAYLOAD` inbound auto-swap and from gas-refund swaps, directly draining protocol-held WPC/PC liquidity or degrading the value users receive from legitimate cross-chain gas top-ups. This is a material, fund-affecting bug, not merely informational — unlike the original Blueberry report (which was more about broken *functionality*), here the analog manifests as an extractable value-transfer vector.

### Likelihood Explanation
Likelihood depends on real-world pool depth, which cannot be confirmed from the static repository (the actual WPC/PRC20 pool liquidity, whether it's seeded with meaningful depth, and whether `defaultFeeTier` selection or any circuit breaker exists elsewhere in the `UniversalCore` Solidity contract, are outside what the indexed Go code shows). The Go-side keeper logic itself contains no additional TWAP, deviation check, or minimum-liquidity gate beyond the fixed 5% band, so if any whitelisted gas-token pool is thin (plausible for newly onboarded chains/tokens, e.g. the `stETH.eth` token config seen in `config/testnet-donut/eth_sepolia/tokens/steth.json`), exploitation is straightforward for any unprivileged user with capital to move the pool. This repository does not have visibility into the `UniversalCore` Solidity contract's swap execution internals (e.g., whether it applies its own TWAP or additional protections), which is a limitation of this analysis — a Devin session with full repo/contract access would be needed to confirm whether `UniversalCore.sol` (in a separate contracts repo) applies any independent price-manipulation defenses beyond what the Go keeper passes in.

### Recommendation
Replace the same-block spot quote with a TWAP-based or externally cross-checked price for computing `minPCOut`, and/or enforce a maximum pool-price deviation from a longer-window average before allowing the auto-swap to proceed; fall back to a no-swap deposit path (already implemented for the gas-refund flow) when quoted price deviates beyond a safe bound or when pool liquidity is below a configured floor.

### Proof of Concept
Conceptual sequence:
1. Admin whitelists a low-liquidity gas token (`TokenConfig`) with a PRC20 native representation, and a corresponding PRC20/WPC Uniswap V3 pool exists on Push Chain with shallow depth.
2. Attacker submits an ordinary EVM swap against the PRC20/WPC pool to shift the spot price favorably for themselves (e.g., pushing WPC price down relative to PRC20).
3. Attacker (or an accomplice) triggers a `GAS` inbound (a normal cross-chain gas top-up) for that token; once UVs vote it in, `ExecuteInboundGas` calls `GetSwapQuote` against the now-manipulated pool and computes `minPCOut` from that skewed price, then executes `CallPRC20DepositAutoSwap` at the same skewed price.
4. Attacker receives an inflated PC (via WPC) amount relative to the token's real market value, extracting value from the pool/protocol; repeating this against a thin pool scales the extraction.

This cannot be fully executed/validated without the `UniversalCore.sol` contract and a running testnet, which are outside the indexed Go repository content available here.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/evm.go (L500-537)
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
```

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
```

**File:** x/uexecutor/keeper/outbound.go (L259-270)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
}
```
