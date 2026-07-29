Confirmed: there is no `quote == 0` / `minPCOut == 0` guard anywhere in `x/uexecutor/keeper` around these swap-quote-based slippage computations. This is the valid, in-scope analog.

### Title
Unchecked zero swap quote lets slippage protection round to zero for gas-abstraction and refund auto-swaps - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Push Chain's gas-abstraction inbound flow, gas+payload inbound flow, and outbound gas-refund flow all compute a Uniswap `minPCOut` slippage bound from an on-chain `QuoterV2.quoteExactInputSingle` quote as `quote * 95 / 100` using Go `big.Int` integer division, and then immediately pass that value as the swap's minimum-output guard to `depositPRC20WithAutoSwap` / `refundUnusedGas`, without ever checking that `quote` (or the derived `minPCOut`) is non-zero. This mirrors the audited `splits-oracle` M-02 bug class: an on-chain quote can legitimately round to zero (extreme price ratio, thin liquidity, or a small `amount` relative to the token's real value), and the resulting zero minimum-output check silently disables slippage protection for that swap, rather than aborting.

### Finding Description
Three call sites compute the swap slippage floor the same way: [1](#0-0) [2](#0-1) [3](#0-2) 

All three derive `quote` from `GetSwapQuote`, which is a live call to `QuoterV2.quoteExactInputSingle`: [4](#0-3) 

`quote` is attacker-influenceable: the inbound `amount` (source of `quote`) is a value the depositing user fully controls, and the resulting output amount depends on the current AMM pool price/reserves for the `prc20 → WPC` pair — a pair the validator set does not control and which can be thin or heavily skewed for newly listed / low-liquidity PRC20 tokens. If `quote` is 0 or so small that `quote*95` integer-divides to 0 (i.e. `quote <= 1` in raw base units), `minPCOut` becomes `0`, and the code proceeds to call `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` with `minPCOut = 0` and no error/rejection path. No code path in `x/uexecutor/keeper` checks `quote.Sign() == 0` or `minPCOut.Sign() == 0` before executing the swap (confirmed by grep across the package).

A swap submitted with `minPCOut = 0` has no slippage protection at all: an attacker who can influence transaction ordering around the module's EVM-originated swap (e.g., via a sandwich against the pool the module swaps through) can move the pool price arbitrarily before the module's swap executes and capture essentially the entire input value, since the contract-side check `amountOut >= minPCOut` is trivially satisfied by `amountOut >= 0`.

### Impact Explanation
This falls within the allowed "unauthorized module-originated EVM execution" / "corruption of ... gas fee accounting, refund accounting" impact category: an unprivileged external actor can cause the `uexecutor` module (acting as `ueModuleAccAddress`) to execute a Uniswap swap on behalf of a user's deposit or gas refund with zero minimum-output protection, letting the value of that PRC20-to-WPC (or PRC20-to-native) swap be extracted via price manipulation instead of being credited to the user's UEA or refund recipient. This directly reduces user/protocol-controlled funds delivered through the universal execution and refund accounting paths.

### Likelihood Explanation
Triggering the exact `quote == 0/1` boundary requires either a very small deposit relative to the AMM pool's price scale or a newly-created/thin-liquidity pool with an extreme price ratio between the PRC20 and WPC — plausible for freshly onboarded low-liquidity tokens or a user submitting a dust-sized cross-chain deposit, both of which are reachable via ordinary unprivileged user deposit/refund flows without any validator or admin cooperation. It is not the most probable everyday case, but it does not require any privileged actor and is fully attacker-triggerable by choosing token/amount pairs.

### Recommendation
Add an explicit zero check immediately after computing `minPCOut` (and/or after fetching `quote`) at all three call sites, aborting/reverting (or marking the inbound/outbound for revert with a clear reason) instead of silently proceeding with an unprotected swap:
```go
if quote == nil || quote.Sign() <= 0 {
    execErr = fmt.Errorf("swap quote is zero or invalid")
    shouldRevert = true
}
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
if minPCOut.Sign() <= 0 {
    execErr = fmt.Errorf("computed minPCOut is zero, refusing unprotected swap")
    shouldRevert = true
}
```
Apply the same guard to `gasAndPayloadDepositAutoSwap` and `applyGasRefund`'s swap-refund branch (falling back to the existing no-swap refund path when the quote is zero, which that code already supports for other error cases).

### Proof of Concept
1. Identify or create a PRC20/WPC Uniswap pool with an extreme effective price ratio (e.g., a newly deployed low-liquidity pool, or a token whose price relative to WPC is very high, analogous to `BTC/SHIB`), or submit an inbound deposit with a very small `amount`.
2. Submit a cross-chain gas-abstraction deposit (`ExecuteInboundGas`) or gas+payload deposit for that token/amount so that `GetSwapQuote` returns `quote` such that `quote*95/100` truncates to `0` (i.e., `quote` is 0 or 1 in raw units).
3. Observe that `CallPRC20DepositAutoSwap` is invoked with `minPCOut = 0`, with no prior validation rejecting the zero value.
4. An attacker who can influence the pool price around the timing of this module-originated swap (e.g., sandwiching the underlying pool transaction) can capture the swapped value since any non-negative `amountOut` satisfies the `>= 0` minimum-output check on-chain.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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
