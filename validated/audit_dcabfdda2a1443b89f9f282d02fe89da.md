## Finding: Sandwichable spot-price AMM quote used for auto-swap minPCOut, enabling value extraction from inbound gas-abstraction and refund swaps

### Title
Manipulable Uniswap V3 spot-price quote (`GetSwapQuote`) with fixed 5% slippage enables sandwich extraction of value from PRC20 auto-swap deposits and gas refunds — ([File: x/uexecutor/keeper/evm.go], [File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/outbound.go])

### Summary
The GAS / GAS_AND_PAYLOAD inbound execution path and the outbound gas-refund path both price a PRC20→WPC swap using a single, un-TWAP'd spot quote from Uniswap V3's `QuoterV2.quoteExactInputSingle`, then apply only a fixed 5% slippage tolerance before executing the real swap on-chain. Because the quote is read from the live, on-chain Uniswap V3 pool that lives on Push Chain itself, an ordinary unprivileged user can move that pool's price with an ordinary front-run/back-run pair of transactions around the block that finalizes the inbound/outbound vote, extracting value from the depositor/refund recipient — the same "AMM spot price manipulated by an unprivileged actor around a critical valuation" bug class as the external report's `getAmountsIn()` finding, just adapted to the Uniswap V3 QuoterV2 call used inside Push Chain's own module-originated EVM calls.

### Finding Description
`Keeper.GetSwapQuote` [1](#0-0)  calls `QuoterV2.quoteExactInputSingle` with `commit=false`, i.e. it reads the *current* spot price of the on-chain Uniswap V3 pool between the inbound PRC20 gas token and WPC. This is used in two places:

1. **Inbound gas-abstraction auto-swap** — `ExecuteInboundGas` fetches the quote and computes `minPCOut = quote * 95 / 100` immediately before calling `CallPRC20DepositAutoSwap`, which performs the actual swap on-chain: [2](#0-1) 

2. **Outbound unused-gas refund** — `applyGasRefund` does the identical pattern (`getSwapQuoteForRefund` → `minPCOut = quote*95/100` → `CallUniversalCoreRefundUnusedGas` with `withSwap=true`): [3](#0-2) 

Both call sites read the quote and execute the swap back-to-back within the same keeper function, so there is no interleaving *within* that single transaction. However, the pool being priced is Push Chain's own Uniswap V3 pool, and the inbound/outbound processing that triggers these swaps happens in an ordinary, unprivileged, user-submitted `MsgVoteInbound`/`MsgVoteOutbound` finalization transaction. An attacker who is not a validator, TSS participant, or any privileged role can:

- Observe (or, for their own inbound, know in advance) that a deposit's auto-swap or a refund swap is about to execute in an upcoming block.
- Submit an ordinary swap transaction against the same PRC20/WPC Uniswap V3 pool immediately before the finalizing vote transaction is included, skewing the spot price against the depositor/recipient (still within the 5% band the code tolerates, or by picking pools/fee tiers where price impact from a given trade size safely clears 5%).
- Let the module's auto-swap execute at the manipulated price, minting less PC to the victim (or refunding less PC) than fair value.
- Submit a back-run transaction reverting the pool to its prior price, capturing the arbitrage difference.

The upgrade log explicitly documents that this feature replaced a prior "0-slippage" call with "5% slippage from a QuoterV2 quote" [4](#0-3) , confirming the design relies on an instantaneous spot quote plus a fixed percentage band rather than a manipulation-resistant TWAP or external price oracle — exactly the pattern the external report recommends against.

### Impact Explanation
Value is siphoned from ordinary users' inbound deposits (they receive less PC than the swap should have yielded) or from the protocol's gas-refund payouts, into the pocket of whoever sandwiches the swap. This is a direct, reachable-by-unprivileged-attacker corruption of PRC20/native asset accounting: the amount of PC actually credited to the UEA (`CallPRC20DepositAutoSwap`'s `minPCOut`/actual swap output) or refunded (`CallUniversalCoreRefundUnusedGas`) diverges materially from fair value, which is in-scope under "corruption of PRC20 or native asset accounting ... refund accounting ... reachable from ordinary user deposits."

### Likelihood Explanation
Likelihood is meaningful whenever the pool for a given PRC20/WPC pair has thin liquidity (new or low-volume gas tokens), since a modest trade can move price by more than the 5% tolerated band, or an attacker can size trades to stay just inside 5% while still extracting the delta. The trigger requires no privileged role — only submitting ordinary EVM swap transactions timed around a target inbound/outbound processing block, which is routine MEV activity.

### Recommendation
Do not price the auto-swap solely from an instantaneous `QuoterV2.quoteExactInputSingle` spot read. Use a manipulation-resistant reference (e.g., a TWAP over a sufficiently long window, or an external price oracle for the gas token/PC pair) to bound `minPCOut`, and/or tighten slippage checks by comparing the pre-trade and post-trade pool price rather than a flat 5% off a single spot read. Consider also capping/limiting swap size relative to pool depth, or routing through multiple fee tiers/pools with combined TWAP protection.

### Proof of Concept
1. Attacker identifies (or triggers, as the original depositor) an upcoming `ExecuteInboundGas` auto-swap for PRC20 token `X`/WPC pool with low liquidity.
2. Attacker submits tx A: large swap of `X`→WPC (or WPC→X) in the same Uniswap V3 pool used by `GetUniversalCoreQuoterAddress`/`GetSwapQuote`, skewing spot price.
3. The `MsgVoteInbound` finalization tx executes in the following/same block: `GetSwapQuote` reads the skewed price, computes `minPCOut = quote*0.95`, and `CallPRC20DepositAutoSwap` executes the real swap at the skewed price — victim's UEA receives less PC than fair value [5](#0-4) .
4. Attacker submits tx B reversing tx A, restoring the pool and realizing the arbitrage profit corresponding to the value lost by the victim's deposit. [1](#0-0)

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-526)
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

```

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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
