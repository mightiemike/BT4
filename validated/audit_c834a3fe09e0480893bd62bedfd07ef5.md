### Title
Gas-abstraction auto-swap uses manipulable instantaneous Uniswap V3 quote instead of a TWAP, enabling same-block sandwich extraction of inbound user funds - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
`ExecuteInboundGas` computes slippage protection for the mandatory PRC20→WPC "gas abstraction" auto-swap using `GetSwapQuote`, which calls Uniswap V3 `QuoterV2.quoteExactInputSingle` — a function that reads the pool's *current* `sqrtPriceX96` state, not a time-weighted average. This spot-price quote is then used, in the same call, to derive `minPCOut` and immediately execute `CallPRC20DepositAutoSwap`. Because the pool's spot price can be moved arbitrarily by an unprivileged actor performing an ordinary swap in the same block, this is the direct on-chain analog of the LP-pricing flashloan-manipulation bug class: value transferred by the protocol (the user's inbound PRC20 principal) is priced against an attacker-influenced instantaneous market price rather than a manipulation-resistant oracle.

### Finding Description [1](#0-0) 

`ExecuteInboundGas` runs as part of inbound finalization for `TxType_GAS` inbounds [2](#0-1) . For every such inbound it:
1. Fetches the default fee tier and quoter/WPC addresses from `UniversalCore`.
2. Calls `GetSwapQuote`, which invokes `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (no price-limit protection) [3](#0-2) . This method simulates the swap against the pool's live tick/price state — it is a spot quote, not a TWAP-derived, manipulation-resistant price.
3. Applies a flat 5% slippage tolerance to that spot quote to compute `minPCOut`.
4. Immediately executes the swap via `CallPRC20DepositAutoSwap` using that `minPCOut` as the sole protection against adverse execution.

Because the reference price and the swap execution both derive from the *same* manipulable pool state, an attacker who moves the pool's price before this logic runs (e.g., by submitting a large swap transaction ordered earlier in the same block, since Cosmos SDK processes transactions/EndBlocker logic within a single block deterministically by order) can force the "protected" swap to execute at an attacker-favorable rate. The 5% band is trivially satisfied because the reference price itself reflects the manipulation — this is architecturally identical to the reported LP-pricing bug, where `burnAsset`/`setEYEBasedAssetStake` priced fate off manipulable reserve ratios instead of a robust price source.

This reachable by an ordinary unprivileged user: submitting a normal cross-chain gas-abstraction deposit (an inbound of `TxType_GAS`) is a standard user flow, and moving the WPC/PRC20 pool spot price via a normal swap requires no privileged access, validator collusion, or external chain compromise — only capital and same-block transaction ordering, both attacker-controlled.

### Impact Explanation
This corrupts PRC20/native asset accounting for gas-abstraction inbound: the protocol/module executes an auto-swap of the user's deposited principal using a self-referential, attacker-movable price rather than a robust oracle, converting the affair into a same-block sandwich. This directly maps to the "Registry and accounting path" and "Universal execution path" impact categories in scope (PRC20 accounting corruption, wrong `UniversalTx`/swap outcome from module-originated execution). The result is value extraction from either the depositing user (worse conversion rate than fair market) or the protocol (module-held liquidity), at attacker profit — the same fate-inflation-style value drain pattern as the original finding, translated to Push Chain's PRC20 auto-swap accounting.

### Likelihood Explanation
Medium-High. It requires only an unprivileged actor to (a) have or borrow capital to move the specific WPC/PRC20 pool used for a given `defaultFeeTier`, and (b) get their manipulating swap included in the same block ahead of the `ExecuteInboundGas` execution for a targeted inbound (achievable via normal mempool/ordering or repeated attempts, since inbound processing timing is somewhat predictable once ballots finalize). No validator, relayer, or TSS compromise is needed — matching the "ordinary user deposits ... alone" allowed-impact bar. Liquidity depth of the specific PRC20/WPC pool determines attack cost, similar to the original finding's dependence on pool depth.

### Recommendation
Do not use an instantaneous `QuoterV2.quoteExactInputSingle` spot quote as the sole reference/slippage basis for module-executed swaps. Use a manipulation-resistant reference price (TWAP oracle over multiple blocks, e.g., Uniswap V3's `observe`/TWAP oracle, or an off-chain price feed cross-checked against spot), and/or bound `minPCOut` using a maximum allowed deviation from that TWAP rather than a flat percentage of the (attacker-influenceable) spot quote. Also consider setting `SqrtPriceLimitX96` bounds to cap worst-case execution price independent of the quote itself.

### Proof of Concept
1. Attacker identifies a low-liquidity PRC20↔WPC pool used by `UniversalCore.defaultFeeTier` for some inbound source-chain gas token.
2. Attacker submits an ordinary large swap transaction against that pool (using their own funds or a flashloan-equivalent capital source) that shifts the pool's spot price unfavorably against the eventual PRC20→WPC direction, ordering it to land in the same block before a target inbound's `ExecuteInboundGas` execution.
3. A user's ordinary cross-chain gas-abstraction deposit (`TxType_GAS` inbound) finalizes in that same block; `ExecuteInboundGas` calls `GetSwapQuote` → `QuoterV2.quoteExactInputSingle`, which now reflects the manipulated pool state [4](#0-3) .
4. `minPCOut` is computed as 95% of this already-degraded quote [5](#0-4) , so `CallPRC20DepositAutoSwap` executes the user's PRC20 principal into WPC at the manipulated rate without reverting.
5. Attacker reverses their initial swap in the same or next block, capturing the price impact/fees paid by the module's auto-swap as profit, while the depositing user (or protocol-held liquidity) receives materially less WPC than fair value.

Note: I was not able to fully trace the exact block-processing order guarantee between an attacker's ordinary EVM swap tx and the EndBlocker/ballot-finalization step that triggers `ExecuteInboundGas` within this index (this detail would require examining the ABCI hooks / EndBlocker wiring for `x/uexecutor`, which wasn't in the retrieved context). If a Devin session with full repository access is available, this ordering should be confirmed to solidify same-block feasibility versus multi-block feasibility (the latter still works given persistent manipulation across a short window).

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

**File:** x/uexecutor/keeper/execute_inbound.go (L18-20)
```go
	switch utx.InboundTx.TxType {
	case types.TxType_GAS: // fee abstraction
		return k.ExecuteInboundGas(ctx, *utx.InboundTx)
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
