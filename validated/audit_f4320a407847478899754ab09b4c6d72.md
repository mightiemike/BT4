### Title
Hardcoded 5% swap-slippage tolerance on inbound GAS/GAS_AND_PAYLOAD auto-swap enables MEV sandwich extraction - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
The `x/uexecutor` module executes an on-chain AMM swap (PRC20 → WPC) whenever a user's inbound deposit is `GAS` or `GAS_AND_PAYLOAD`, and again when refunding unused relayer gas on outbound finalization. In all three call sites the minimum-out slippage protection is computed as a hardcoded 5% tolerance (`quote * 95 / 100`) with no module parameter or governance control, mirroring the reported `AaavePositionManager` `TOLERANCE_BIPS` issue (2% hardcoded vs. 0.5% expected).

### Finding Description
`GetSwapQuote` fetches a spot quote from the Uniswap V3 `QuoterV2` contract for the exact amount being deposited [1](#0-0) . Immediately afterward, `minPCOut` is derived by hardcoding a 5% slippage allowance instead of using a configurable, tighter tolerance:

- Inbound `GAS` execution: [2](#0-1) 
- Inbound `GAS_AND_PAYLOAD` execution (`gasAndPayloadDepositAutoSwap`): [3](#0-2) 
- Outbound gas-refund swap (`applyGasRefund`): [4](#0-3) 

All three literally compute `minPCOut := new(big.Int).Mul(quote, big.NewInt(95)); minPCOut.Div(minPCOut, big.NewInt(100))`, and none of them read from a module `Params` type or any other configurable source — I confirmed there is no `SlippageBips`, `MinPCOut`, or `Tolerance` field anywhere in the `x/uexecutor` proto/types (`proto/uexecutor/v1/types.proto`, `x/uexecutor/types/types.pb.go`), and the constant is inlined in three separate keeper files rather than centralized. This is functionally identical to the reported `AaavePositionManager` bug: a swap-tolerance value baked into the contract/module logic at a level (5%) far above the ~0.5%–1% typically used for liquid pairs, rather than being configurable.

The quote used for `minPCOut` is a same-block spot quote from `QuoterV2.quoteExactInputSingle`, not a TWAP. Because the entire swap sequence (deposit + quote + auto-swap) executes deterministically inside the inbound ballot finalization / outbound-observation finalization flow, an attacker who can predict or observe when a given inbound/outbound ballot is about to reach quorum (this is derivable from on-chain vote counts, which are public) can manipulate the WPC/PRC20 pool price on Push Chain in the block(s) immediately preceding finalization, then reverse the manipulation after the module's auto-swap executes at the wide 5% tolerance, extracting value from the deposited/refunded user funds — a classic sandwich attack against a protocol-controlled swap.

### Impact Explanation
This affects protocol-controlled funds moving through `depositPRC20WithAutoSwap` (deposit auto-swap for inbound `GAS`/`GAS_AND_PAYLOAD`) and `refundUnusedGas` (outbound gas refund swap). A wide, non-adjustable 5% tolerance allows an MEV actor to extract up to (close to) that margin from every swap executed by the module, degrading the amount of PC native token users actually receive on deposit or refund. This is a value-extraction/fund-loss issue against ordinary users' deposited/refunded funds, matching the in-scope impact category of "stealing … funds" via "corruption of … refund accounting" in the universal execution flow.

### Likelihood Explanation
Medium. Exploitation requires the attacker to be able to manipulate the on-chain PRC20/WPC pool price on Push Chain around the specific block where a given inbound or outbound ballot finalizes and the auto-swap fires. Ballot vote counts and thresholds are public state, so the finalizing block is often predictable a block or two in advance, making standard sandwich tooling applicable. No privileged access, malicious validator, or malicious relayer is required — a purely external, unprivileged actor with capital and access to the Push Chain mempool/pool can attempt this on every qualifying inbound/outbound.

### Recommendation
- Replace the hardcoded `big.NewInt(95)/big.NewInt(100)` tolerance in `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `outbound.go` with a single governance-configurable module parameter (e.g., `Params.SwapSlippageBps`), defaulting to a materially tighter value (e.g., 50–100 bps) rather than 500 bps.
- Centralize the slippage computation into one helper (e.g., `k.ComputeMinOut(quote, params.SwapSlippageBps)`) so the three call sites cannot drift.
- Consider using a manipulation-resistant reference price (e.g., a TWAP or an oracle-cross-check) rather than a single spot `QuoterV2` quote taken in the same transaction as the swap, to reduce the attack surface independent of the tolerance value chosen.

### Proof of Concept
1. Monitor `x/uvalidator` ballot state for an in-flight `GAS`/`GAS_AND_PAYLOAD` inbound (or an outbound awaiting observation) that is close to reaching its voting threshold.
2. Immediately before the finalizing vote transaction is expected to land, submit a large swap against the same PRC20/WPC Uniswap V3 pool on Push Chain to move the spot price unfavorably for the user's PRC20 amount.
3. When the ballot finalizes, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`/`applyGasRefund` calls `GetSwapQuote` (reflecting the manipulated price) and executes `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` with `minPCOut = quote * 95/100`, which still passes even though the user receives up to ~5% less PC than they would have at the unmanipulated price.
4. Immediately after, reverse the manipulating swap, capturing the difference as attacker profit while the user's deposited/refunded value is diminished.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
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
