### Title
GAS/GAS_AND_PAYLOAD inbound autoswap prices trades from a manipulable spot quote, allowing sandwich extraction of user-deposited funds - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go)

### Summary
When a `GAS` or `GAS_AND_PAYLOAD` inbound is finalized, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` compute the amount of native PC (WPC) a user should receive for their deposited PRC20 by calling `GetSwapQuote`, which invokes Uniswap V3 `QuoterV2.quoteExactInputSingle` directly against the live pool (spot price), then executes the deposit-and-swap with only a flat 5% slippage tolerance (`minPCOut = quote * 95 / 100`). [1](#0-0) [2](#0-1) 

### Finding Description
This is the same bug class as the Malt report: a protocol-critical trade-size/price calculation reads directly from an AMM pool's instantaneous state instead of a manipulation-resistant TWAP, and that value is then used to execute a real trade with only a fixed percentage slippage guard rather than a guard derived from a robust price source.

Here, `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` on the configured `uniswapV3Quoter` for the `prc20 -> WPC` pair using `GetDefaultFeeTierForToken` fee tier [1](#0-0) , and the caller computes `minPCOut` as a straight 95% of that quote before calling `CallPRC20DepositAutoSwap`, which performs the actual on-chain swap through the UniversalCore/Uniswap V3 router [3](#0-2) . The same pattern repeats in `gasAndPayloadDepositAutoSwap` for `GAS_AND_PAYLOAD` inbounds and in `getSwapQuoteForRefund` used for unused-gas refunds on outbound completion [4](#0-3) [5](#0-4) .

An unprivileged attacker who observes that an inbound is about to be finalized (once 2/3 of Universal Validators have voted, finalization and this swap execute automatically in the block containing the last confirming vote) can, within the same block, front-run with a large swap against the same Uniswap V3 pool to push the spot price away from fair value, let the module's quote-and-swap execute against the manipulated price (bounded only by the 5% slippage floor), and then back-run to restore the pool and capture the difference. Because the check is against the pool's own current price rather than an external/TWAP reference, moving the pool by more than 5% (any pool with moderate depth relative to the attacker's capital) fully defeats the slippage protection, and the attacker extracts value up to that bound directly from the deposited user funds being auto-swapped.

### Impact Explanation
This directly corrupts PRC20/native asset accounting for ordinary user gas-abstraction deposits: the user receives a manipulated (up to 5% worse, floor-bound) amount of native PC for their bridged tokens, and the difference is captured by the attacker via the sandwich, constituting unauthorized value extraction from protocol/user-controlled funds during a default, reachable execution path (`ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, and gas-refund swap). This falls within the in-scope "corruption of PRC20 or native asset accounting" and "stealing ... of user or protocol-controlled funds" categories, reachable by an ordinary unprivileged user/attacker with no reliance on malicious validators — only public knowledge of pending, about-to-finalize honest UV votes and normal DEX interaction.

### Likelihood Explanation
Likelihood is comparable to the original Malt finding: technically executable but requires the attacker to have capital to move the specific pool by a meaningful percentage and to land transactions before/after the finalizing vote transaction in the same block (feasible via mempool monitoring or block-builder relationships, since UV votes are broadcast as ordinary transactions before finalization). The magnitude of extractable value is capped by the 5% slippage tolerance and the pool's liquidity depth, so the incentive scales with deposit size and pool thinness — likely to affect smaller/newer PRC20-WPC pools most.

### Recommendation
Do not price real swaps off a single, same-block spot quote. Either (a) derive `minPCOut` from an external, resistant-to-single-block-manipulation observation (e.g., a time-weighted average obtained from Uniswap V3 `observe`/TWAP over a window, similar to the report's own recommendation) or a governance-configured reference price, in addition to a tighter dynamic slippage bound, or (b) route through a mechanism that lets the quote and execution be separated by validator consensus (e.g., using the already-existing UV median-vote pattern used for `ChainMeta`/gas price) rather than an ad hoc on-demand `QuoterV2` call executed atomically with the swap.

### Proof of Concept
1. Wait for an inbound `MsgVoteInbound` from the last required Universal Validator that will push the ballot to 2/3+ threshold and trigger `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` in that block.
2. In the same block, submit a transaction that swaps a large amount into/out of the `prc20/WPC` Uniswap V3 pool at the fee tier returned by `GetDefaultFeeTierForToken`, moving the spot price by more than 5%.
3. The finalizing vote transaction executes `ExecuteInboundGas`, which calls `GetSwapQuote` (reading the now-manipulated spot price) and computes `minPCOut = quote * 95 / 100`, then calls `CallPRC20DepositAutoSwap`, executing the deposit-and-swap against the manipulated pool state.
4. Submit a reverse transaction to restore the pool price and capture the spread, at the expense of the value the depositing user should have received in native PC. [1](#0-0) [3](#0-2)

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
