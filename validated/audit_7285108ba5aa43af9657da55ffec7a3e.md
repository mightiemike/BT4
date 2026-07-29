## Title
Auto-Swap Slippage Protection Uses a Manipulable Spot AMM Price as Its Own Oracle - (File: x/uexecutor/keeper/evm.go)

## Summary
The Nemeos report describes a lending protocol that depends on an external floor-price oracle that borrowers can manipulate (wash trading, coordinated listing manipulation) to drain the pool. Push Chain has a structurally identical pattern in its own on-chain code: when the `uexecutor` module auto-swaps a bridged gas-token PRC20 into WPC (native gas token) on inbound GAS / GAS_AND_PAYLOAD execution, and when it refunds unused destination-chain gas after an outbound vote, the "slippage protection" bound (`minPCOut`) is derived from the exact same instantaneous Uniswap V3 spot price that the swap itself will execute against, with no TWAP, staleness check, or external reference price.

## Finding Description
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` to get an expected output amount for a `prc20 → wpc` swap: [1](#0-0) 

This quote is read at the moment of execution and then used, unmodified except for a flat 5% haircut, as the minimum-out bound passed into the real swap: [2](#0-1) 

The same pattern is repeated for `GAS_AND_PAYLOAD` inbound execution and for post-outbound gas refunds: [3](#0-2) [4](#0-3) 

Because `minPCOut = quote * 95 / 100` is computed from the *same* on-chain pool state that will immediately be traded against, the "slippage protection" only guards against price movement *between* the quote call and the swap call within the same keeper invocation — it provides zero protection against the pool price having already been pushed to an unfavorable level by an ordinary user transaction executed just before the module's derived call lands in the same or an adjacent block. This is architecturally the same flaw as an NFT lending protocol trusting a floor-price oracle that reflects recent wash-traded/manipulated listings: the "ground truth" consulted to bound the transaction is itself attacker-influenced state, sampled at a single point in time with no resistance to manipulation (no TWAP window, no external reference, no maximum price-impact cap independent of the same pool).

An unprivileged actor can:
1. Trigger a real inbound bridging event on an external chain of type `GAS` or `GAS_AND_PAYLOAD` (this is a normal user action — no privileged role required), which will eventually be finalized and executed via `ExecuteInboundGas` / `ExecuteInboundGasAndPayload` once UV votes pass.
2. Race a large ordinary swap transaction against the same gasToken/WPC Uniswap V3 pool on Push Chain into the block (or shortly before) where the module's auto-swap executes, moving the pool's spot price so that `quoteExactInputSingle` returns a value skewed in the attacker's favor.
3. Because `minPCOut` is derived from that same skewed quote, the auto-swap executes at the manipulated price instead of a fair market price, extracting value from the pool's WPC/PRC20 reserves (funds effectively belonging to the protocol/liquidity providers and other bridging users) at the expense of the party whose deposit is being auto-swapped.

## Impact Explanation
This falls under "corruption of PRC20 or native asset accounting" and potential fund drain via manipulated swap execution reachable from an ordinary unprivileged user's bridging deposit — no privileged validator, relayer, or admin action is required to trigger the vulnerable code path (only the standard inbound flow that any bridging user goes through). The financial impact scales with pool depth and the size of the bridged amount; thinly-liquidity WPC/gas-token pools (explicitly called out as "tiny" in the e2e setup script) make this materially exploitable.

## Likelihood Explanation
Likelihood is moderate-to-high: triggering a GAS/GAS_AND_PAYLOAD inbound is a standard, unprivileged user action, and front-running/sandwiching a spot-price-based AMM quote with no TWAP protection is a well-understood, low-cost attack pattern requiring only normal swap transactions on the pool, not any privileged capability.

## Recommendation
Do not derive the slippage floor from the same instantaneous quote that the swap will execute against. Use a time-weighted average price (TWAP) over a meaningful window (or an independent price reference) to compute `minPCOut`, and/or enforce a maximum allowed price-impact/deviation between the TWAP and the spot quote before allowing the auto-swap to proceed, falling back to the no-swap deposit path if the deviation exceeds a safe threshold.

## Proof of Concept
1. Deploy/observe the gasToken/WPC Uniswap V3 pool used by `UniversalCore` for a given PRC20 gas token (shallow liquidity, per e2e setup).
2. Submit a real bridging deposit (GAS or GAS_AND_PAYLOAD inbound) for a sizeable amount on the relevant external chain; wait for UVs to observe it.
3. Immediately before/at the block where the finalized inbound is executed on Push Chain (`ExecuteInboundGas` calling `GetSwapQuote` then `CallPRC20DepositAutoSwap`), submit an ordinary large swap against the same pool to shift its spot price.
4. Observe that `GetSwapQuote` returns the skewed price and `minPCOut = quote*95/100` still permits the auto-swap deposit to execute at the manipulated rate, extracting value from the pool.

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-522)
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
