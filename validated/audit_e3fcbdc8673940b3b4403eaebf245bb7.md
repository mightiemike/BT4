## Title
Value theft from gas top-up and gas-refund swaps via spot-price manipulation of the UniversalCore Uniswap V3 pool used by `GetSwapQuote` / `CallPRC20DepositAutoSwap` — (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`x/uexecutor` uses a Uniswap V3 `QuoterV2.quoteExactInputSingle` spot-price read to derive `minPCOut` for on-chain PRC20→WPC swaps performed during inbound GAS/GAS_AND_PAYLOAD processing and during outbound gas-fee refunds. The `minPCOut` slippage bound (95% of the just-fetched quote) is calculated purely from the same pool's instantaneous reserves, exactly like the DODO report's reliance on `UniswapV2Library.getAmountsIn()`. Because there is no TWAP, external oracle check, or protocol-level price sanity bound, an unprivileged trader can skew the WPC/PRC20 pool before the module's swap lands, causing the module (and therefore the cross-chain user) to receive a price-manipulated (up to ~5% worse, or worse if pools are thin) amount of native PC, while the attacker profits by unwinding the skew afterward — the same bug class acknowledged in the external report, applied to Push Chain's own AMM-quote-based swap logic.

### Finding Description
Three flows in `x/uexecutor` perform a "fetch on-chain AMM quote → apply fixed 5% slippage → execute swap" pattern:

1. **Inbound GAS processing** — `k.GetSwapQuote()` is called, then `minPCOut = quote*95/100`, then `k.CallPRC20DepositAutoSwap()` executes the swap: [1](#0-0) 

2. **Inbound GAS_AND_PAYLOAD processing** — identical pattern in `gasAndPayloadDepositAutoSwap`: [2](#0-1) 

3. **Outbound gas-fee refund** — `applyGasRefund` fetches a quote via `getSwapQuoteForRefund` and applies the same 95% bound before calling `CallUniversalCoreRefundUnusedGas` with `withSwap=true`: [3](#0-2) 

The underlying quote mechanism, `GetSwapQuote`, is a direct call into a live Uniswap V3 `QuoterV2` contract, reading the pool's current (spot) reserves/tick state — no TWAP, no external price feed, no deviation check against a reference price: [4](#0-3) 

This is functionally identical to the DODO root cause: `UniswapV2Library.getAmountsIn()`/`getAmountsOut()` reliance on manipulable spot reserves, mitigated only by a fixed percentage slippage band computed from the very same (potentially already-skewed) price. The 5% band protects against price movement *between* the quote call and the swap call (which in this codebase happen back-to-back in the same keeper invocation with no other tx interleaved, so that specific race is closed), but it does **not** protect against the pool price already being skewed by an attacker's own prior trade(s) at the moment both calls execute. Any unprivileged EVM account can trade against the WPC/PRC20 Uniswap V3 pool used by `UniversalCore` (it is an ordinary, permissionlessly-tradable AMM pool), so:

- An attacker swaps into the pool to move the PRC20/WPC price before the validator-processed inbound (or outbound refund) lands.
- The module's swap executes against this skewed price; `minPCOut` merely bounds acceptable output to 95% of the skewed quote, not 95% of a fair/reference price.
- The attacker reverses the trade afterward, capturing the spread — the cross-chain user (recipient of the gas top-up, or the sender being refunded excess gas) receives correspondingly less native PC.

Because `defaultFeeTier`/pool selection is fixed per PRC20 token and swap sizes for gas top-ups are typically small relative to major liquidity but can still be meaningfully skewed for lower-liquidity gas tokens, thinly-traded PRC20/WPC pairs are the most exposed, mirroring the DODO report's "low-liquidity or custom pool" precondition.

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting" and "stealing ... of user or protocol-controlled funds" in the allowed impact gate. The value lost is bounded per-swap by the slippage tolerance (up to ~5% of the swapped notional) but is systematically extractable by any unprivileged actor against any inbound gas top-up or outbound gas refund that routes through the auto-swap path, with no attacker privilege required and no reliance on malicious validators/relayers — value moves from the cross-chain user/protocol to the attacker.

### Likelihood Explanation
Medium. The attack requires capital to move the pool price and requires reasonably low liquidity in the target PRC20/WPC pool to make the 5% band economically worthwhile, similar to the original DODO precondition. Since pools are ordinary Uniswap V3 pools deployed for PRC20/WPC pairs and open to all traders, and gas top-up/refund swaps are triggered deterministically by inbound/outbound vote finalization (observable on-chain), an attacker can time their sandwich around block production without needing any privileged role.

### Recommendation
- Do not derive the slippage floor solely from the same instantaneous `QuoterV2` call being used for execution; instead bound `minPCOut` against a manipulation-resistant reference (e.g., a TWAP over multiple blocks, or a configured maximum acceptable deviation from a recent moving average / oracle price).
- Consider capping per-block/per-swap notional relative to pool liquidity, or requiring multiple independent price samples before executing large auto-swaps.
- Emit and monitor the realized vs. quoted execution price so anomalous slippage triggers alerting even when within the nominal 5% band.

### Proof of Concept
Conceptual (would need a running Push Chain devnet with a Uniswap V3 WPC/PRC20 pool to fully execute):
1. Deploy/observe a low-liquidity WPC/PRC20 pool used as `defaultFeeTier` pool for a given PRC20 gas token.
2. Attacker (unprivileged EOA) swaps a sizeable amount of WPC into the pool, pushing the PRC20 price of WPC down.
3. Trigger (or wait for) an inbound `GAS` message for that PRC20 to reach ballot finalization; `ExecuteInboundGas` calls `GetSwapQuote` and `CallPRC20DepositAutoSwap`, both against the now-skewed pool — the UEA recipient receives ~5% (or pool-depth-dependent) less WPC than fair value.
4. Attacker reverses their initial trade, restoring the pool and net-profiting the price differential extracted from the module's swap. [5](#0-4)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

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
}
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
