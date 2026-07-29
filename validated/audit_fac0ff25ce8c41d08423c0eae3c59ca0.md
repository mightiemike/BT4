### Title
Auto-swap gas-abstraction path derives its own slippage protection from a spot AMM quote it can be manipulated into — ([File: x/uexecutor/keeper/execute_inbound_gas.go])

### Summary
Push Chain's gas-abstraction inbound flow (`ExecuteInboundGas`) swaps a user's deposited PRC20 gas token for native PC via `depositPRC20WithAutoSwap`, computing the minimum acceptable output (`minPCOut`) as 95% of a spot quote fetched at execution time from Uniswap-V3-style `QuoterV2.quoteExactInputSingle`. Because the slippage floor is derived from the exact same manipulable spot price that determines the swap's actual execution price, an unprivileged external actor who sandwiches or otherwise moves the pool price down before this module-originated swap executes can make both the quote and its 95% floor collapse together, letting the swap complete near-zero output while the user's PRC20 principal is fully consumed. This mirrors the `sellForLP`/`setPrice`-to-zero bug class: the “protection” value is computed from the same corruptible price source it is meant to protect against.

### Finding Description
`ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go`) handles `TxType_GAS` inbounds — an ordinary, unprivileged user action (depositing gas-token funds on an external chain that get relayed and voted on by honest validators). After UEA resolution, it calls: [1](#0-0) 

which fetches `fee` via `GetDefaultFeeTierForToken`, then a spot `quote` via `GetSwapQuote`, and computes `minPCOut = quote * 95 / 100`, before calling `CallPRC20DepositAutoSwap` with that `minPCOut`.

`GetSwapQuote` (`x/uexecutor/keeper/evm.go`) performs a single, non-commit call to `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96` set to `0` (no price-limit bound), i.e., a plain current-block spot-price read: [2](#0-1) 

The same pattern is duplicated in the outbound gas-refund path (`applyGasRefund` / `getSwapQuoteForRefund` in `x/uexecutor/keeper/outbound.go`), where the refund's `minPCOut` is likewise computed as 95% of a live spot quote fetched immediately before the swap: [3](#0-2) 

Because both the executed swap and its own minimum-output floor are read from the same manipulable AMM pool state at swap time, this is not real slippage protection against price manipulation — it only protects against unrelated *execution-time drift* (e.g. two txs in the same block moving price slightly), not against an attacker who deliberately depresses the pool price (e.g., via a large sell into the WPC pool, a flash-loan-funded trade, or MEV sandwich positioning) immediately before the module-originated `depositPRC20WithAutoSwap`/`refundUnusedGas` call executes. If the pool price is driven down, `quote` and thus `minPCOut` are driven down together, so the deposit/refund swap can still "succeed" while returning far less PC than the token's fair value — burning/consuming the user's PRC20 or gas-token principal for negligible native PC in return, which is functionally the same outcome as the referenced `sellForLP` bug where `price` collapsing to zero let synths be burned for zero LP tokens.

### Impact Explanation
An unprivileged attacker who can move the relevant Uniswap pool price (no special role or validator/relayer collusion required — ordinary DEX trading suffices) can cause honest, unprivileged users' `GAS`/`GAS_AND_PAYLOAD` inbound deposits (and outbound gas refunds) to be swapped at an artificially bad rate with a floor that offers no real protection, resulting in a partial loss of user/protocol-controlled funds during otherwise-normal, validator-approved execution. This fits the "corruption of ... gas fee accounting ... refund accounting" and "unauthorized module-originated EVM execution" impact categories, since honest validators/nodes will finalize and execute this swap exactly as coded — the invariant broken is "the module never releases a user's PRC20 for materially less than fair value," not consensus safety.

### Likelihood Explanation
Triggering this only requires ordinary access to the same DEX pool the module trades against (no admin/validator/relayer role, no key compromise) — an attacker can time trades or use flash loans to depress the pool price right before the deterministic module-triggered swap call executes in the block where the inbound/outbound ballot finalizes. The window and cost depend on the specific pool's liquidity depth and the block-scheduling behavior of Push Chain's ballot finalization, which somewhat limits ease of exploitation compared to a purely permissionless single-block sandwich, but the core "min-output derived from the same spot price" flaw offers no defense-in-depth once liquidity is thin or targeted.

### Recommendation
Do not derive `minPCOut` solely from a live single-block spot quote of the same pool being traded against. Use a TWAP-based or otherwise external/reference price feed (or a bounded max-deviation check against a recent oracle/median price) to compute the slippage floor, so the protection is independent of the price being manipulated in the same transaction/block as the swap. Alternatively, cap the acceptable price deviation between successive quotes and revert (falling back to no-swap PRC20 deposit) rather than proceeding with a self-referential floor.

### Proof of Concept
Conceptual (not executed against a live Push Chain deployment):
1. Attacker identifies the WPC/PRC20 pool used by `UniversalCore`'s auto-swap for a given gas token.
2. Attacker trades to depress the pool's spot price for that token pair immediately before a victim's `GAS` inbound is executed (i.e., in the block where the inbound ballot reaches quorum and `ExecuteInboundGas` runs).
3. `GetSwapQuote` returns a depressed `quote`; `minPCOut = quote*95/100` is computed from that same depressed price.
4. `CallPRC20DepositAutoSwap` executes the swap using `minPCOut`, which no longer reflects fair value, so the victim's PRC20 principal is consumed for far less native PC than intended, while the transaction still succeeds within Push Chain's own bounds check.

Note: exact economic feasibility (attack cost vs. extractable value) depends on the deployed Uniswap-style pool's liquidity, which is external to this repository and not verifiable from the indexed code alone.

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

**File:** x/uexecutor/keeper/outbound.go (L213-222)
```go
	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

```
