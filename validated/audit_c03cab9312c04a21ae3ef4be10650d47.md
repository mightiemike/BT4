I have sufficient evidence to write this finding now.

### Title
On-chain QuoterV2 price used as swap reference with fixed 5% slippage tolerance enables sandwich extraction of gasless-inbound and outbound gas-refund autoswaps - (File: x/uexecutor/keeper/execute_inbound_gas.go, execute_inbound_gas_and_payload.go, outbound.go, evm.go)

### Summary
`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`/`getSwapQuoteForRefund` all price a PRC20→WPC swap by calling `GetSwapQuote` (Uniswap V3 `QuoterV2.quoteExactInputSingle`) immediately before executing the swap via `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, and derive `minPCOut` as a flat `quote * 95 / 100`. Because the reference price is read on-chain, in the same block as (and immediately preceding) the actual swap, and the slippage tolerance is a full 5%, an unprivileged actor who observes the pending `MsgVoteInbound`/`MsgVoteOutbound` transaction that triggers this code can sandwich the pool: push the PRC20/WPC price down before the module's swap executes, let the module's swap execute at the worse price (still within the generous 5% band so it doesn't revert), then push the price back and pocket the difference.

### Finding Description
The relevant flow is:
- [1](#0-0) : fetches `fee` and `quote` via on-chain calls, computes `minPCOut = quote*95/100`, then immediately calls `CallPRC20DepositAutoSwap`.
- [2](#0-1) : same pattern in `gasAndPayloadDepositAutoSwap`, used for the `GAS_AND_PAYLOAD` inbound path.
- [3](#0-2) : same pattern for the outbound gas refund, calling `getSwapQuoteForRefund` then `CallUniversalCoreRefundUnusedGas` with `minPCOut = quote*95/100`.
- [4](#0-3) : `GetSwapQuote` reads the *current* spot-derived quote from `QuoterV2.quoteExactInputSingle` — not a TWAP — at execution time.

All three call sites use the exact same fixed `* 95 / 100` (5%) slippage floor, hard-coded at the call site rather than derived from any market-depth or volatility analysis. Because:
1. The quote and swap are both executed as part of processing a single `MsgVoteInbound`/`MsgVoteOutbound` transaction from a Universal Validator (the vote that reaches quorum), which is itself an ordinary, publicly-broadcast Cosmos transaction sitting in the mempool before being included in a block;
2. The underlying Uniswap V3 pool (`quoterAddr`/pool referenced by `fee` tier) is a normal, publicly tradable AMM pool that any unprivileged address can trade against in the same or an adjacent transaction;
3. The slippage tolerance is a flat 5%, independent of trade size or pool depth,

an attacker can front-run the validator's quorum vote transaction with a large swap that moves the PRC20/WPC price down by up to just under 5%, allow the validator's `depositPRC20WithAutoSwap` / `refundUnusedGas` swap to execute against this manipulated price (it will still clear `minPCOut` since the tolerance band absorbs the full manipulation), and then immediately back-run to restore the price and realize the arbitrage profit extracted from the module's swap. This directly reduces the amount of native PC actually credited to the recipient's UEA (for the gas-abstraction inbound) or to the refund recipient (for the outbound gas refund), since the swap executes near the worst-allowed price rather than the fair market price. This is the same bug class as UnionDAO M-5: a price/exchange-rate reference computed from on-chain state that a value-transfer operation is immediately (and predictably) executed against, allowing an unprivileged actor to sandwich the reference-price window and capture value at the expense of the party the swap is meant to benefit.

### Impact Explanation
This falls in the "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" and "unauthorized ... refund" impact buckets: an unprivileged external attacker can systematically skim value from every `GAS`/`GAS_AND_PAYLOAD` inbound gas-abstraction deposit and every outbound gas refund that uses the swap path, at the direct expense of the user whose funds are being converted (their UEA receives up to ~5% less native PC than fair value on each such transaction). This is a repeatable, mechanical value-extraction path requiring no privileged access, no validator collusion, and no protocol compromise — only mempool visibility and capital to move the pool within the 5% band.

### Likelihood Explanation
Likelihood is high wherever the relevant pool has thin liquidity relative to the deposit/refund size, since a 5% price impact is often achievable with modest capital. The `MsgVoteInbound`/`MsgVoteOutbound` transaction that triggers the swap is a normal, publicly observable Cosmos transaction (submitted by any of the bonded Universal Validators, not a privileged/hidden action), so it can be watched and front-run/back-run like any other mempool transaction targeting an AMM trade. No malicious validator, relayer, or admin behavior is required — only an ordinary MEV searcher watching Push Chain's mempool.

### Recommendation
Do not use a single-block on-chain quote with a fixed percentage slippage tolerance for module-driven swaps that a third party can predict and front-run. Options: (1) use a TWAP-based reference price with a much tighter, size-aware slippage bound instead of a flat 5%; (2) tie the tolerance to observed pool depth/liquidity at call time so it scales down for illiquid pools; (3) route these value-critical swaps through a private/aggregated execution path (e.g., commit-reveal, batch auction, or an internal price oracle) rather than a public AMM quoted and executed in the same observable transaction; (4) at minimum, reduce the slippage tolerance significantly and add a circuit breaker that reverts (falling back to the non-swap deposit/refund path, which already exists) when the achievable price deviates beyond a safe bound from a longer-window reference.

### Proof of Concept
1. Observe the Push Chain mempool for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction from the third Universal Validator that will push a `TxType_GAS` / `TxType_GAS_AND_PAYLOAD` inbound (or a successful outbound with excess gas) over the 2/3 threshold, triggering `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund`.
2. Submit a transaction that swaps a large amount of WPC into the relevant PRC20/WPC Uniswap V3 pool at the `fee` tier returned by `GetDefaultFeeTierForToken`, moving the PRC20 price down by just under 5%, ordered before the validator's vote transaction in the same block.
3. The validator's transaction executes `GetSwapQuote` [4](#0-3)  against the manipulated pool state, computes `minPCOut = quote*95/100` [5](#0-4) , and calls `CallPRC20DepositAutoSwap`, which succeeds at the manipulated (worse) price since it is still within the 5% floor.
4. Submit a back-run transaction reversing the initial swap, restoring the pool price and capturing the arbitrage profit — funded by the reduced PC amount credited to the victim's UEA/refund recipient.

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
