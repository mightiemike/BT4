### Title
Same-block Uniswap V3 QuoterV2 quote used as slippage bound for `GAS`/`GAS_AND_PAYLOAD` inbound swaps enables sandwich-based fund extraction - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
The gas-abstraction inbound flow (`ExecuteInboundGas`) fetches a Uniswap V3 `QuoterV2.quoteExactInputSingle` quote in the *same transaction* that immediately executes the swap, and derives its only slippage protection (`minPCOut`) from that quote with a flat 5% tolerance. Because `quoteExactInputSingle` reads the pool's current (spot) state rather than a time-weighted average, this is the same class of issue as the referenced Maia finding: an unprivileged attacker who can front-run/sandwich the module-originated swap can move the pool price so that the "protective" `minPCOut` bound is computed from an already-manipulated price, defeating the slippage check and letting the attacker extract value from the swap counterparty (protocol/user funds routed through `depositPRC20WithAutoSwap`).

### Finding Description
In `x/uexecutor/keeper/execute_inbound_gas.go` (`ExecuteInboundGas`), step 4 does: [1](#0-0) 

1. `k.GetSwapQuote(...)` calls `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96: 0` (no limit), which returns the current spot-derived output amount for the pool at that instant: [2](#0-1) 
2. `minPCOut` is computed as `quote * 95 / 100` — a flat 5% slippage tolerance based purely on that one spot quote.
3. `CallPRC20DepositAutoSwap` is invoked immediately afterward with this `minPCOut`, executing the actual on-chain swap in the same processing step, no TWAP or external price cross-check is used anywhere in this path.

This mirrors the audited `_gasSwapIn`/`_gasSwapOut` pattern: pulling a manipulable, current-block AMM price and deriving a slippage bound from it, then immediately swapping against it. A user's `GAS`/`GAS_AND_PAYLOAD` inbound (an ordinary, attacker-reachable deposit) triggers this path deterministically, so any MEV actor who can order transactions around the Push Chain block containing this module-originated EVM call (e.g., via searcher bots interacting with the underlying Uniswap V3 pool used by `UniversalCore`) can sandwich it: push the pool price down before the quote is read, let the deposit-swap execute at the manipulated price bounded only by the equally-manipulated `minPCOut`, then reverse the price after, capturing the difference at the expense of the depositing user's PC output.

### Impact Explanation
This directly affects "corruption of ... gas fee accounting ... or canonical UniversalTx state" and "unauthorized ... module-originated EVM execution" adjacent impact categories: the amount of native PC minted/credited to the user's UEA from `depositPRC20WithAutoSwap` can be manipulated downward by an external, unprivileged actor solely by trading against the same pool around the block that processes the inbound. This is a direct loss of user/protocol funds reachable through the ordinary gas-inbound deposit flow, with no privileged actor assumption required (matches in-scope impact: "stealing ... permanent loss ... of user or protocol-controlled funds").

### Likelihood Explanation
Likelihood is comparable to the original C4 finding: it requires only the ability to see the pending Push Chain transaction/inbound processing and to trade in the referenced Uniswap V3 pool around it (a public AMM), i.e., a standard sandwich attack. No relayer, validator, or admin privilege is needed. The `5%` band is a fixed slippage tolerance that does not scale with volatility or provide any resistance since the reference price itself is manipulable within the same window.

### Recommendation
Do not derive `minPCOut` solely from a live `quoteExactInputSingle` spot quote taken immediately before the swap. Use a TWAP-based (time-weighted average) price from the pool's oracle observations (or an external/attested price feed) to compute the slippage bound, or otherwise bound the acceptable deviation between the spot quote and a longer-window reference price before allowing `CallPRC20DepositAutoSwap` to proceed; if the deviation exceeds a safe threshold, defer or revert the swap instead of executing at the manipulated price.

### Proof of Concept
1. Attacker observes an unconfirmed `GAS`/`GAS_AND_PAYLOAD` inbound about to be processed by validators via `ExecuteInboundGas`.
2. Attacker submits a large trade against the PRC20/WPC Uniswap V3 pool referenced by `GetSwapQuote`/`CallPRC20DepositAutoSwap` to push the spot price unfavorably for the pending deposit-swap.
3. Validators execute `ExecuteInboundGas`: `GetSwapQuote` returns a quote based on the now-manipulated pool state; `minPCOut = quote*95/100` inherits the manipulation.
4. `CallPRC20DepositAutoSwap` executes at the manipulated price, delivering the user materially less PC output than a fair-price swap would, while the attacker sells back into a "clean" pool state afterward, capturing the spread. [3](#0-2) [4](#0-3)

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
