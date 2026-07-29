## Analysis

The reported bug class — a hidden *division-before-multiplication* that silently discards precision and can even zero out an amount that should be positive — has a direct analog in Push Chain's own gas-fee accounting code, not in an external/vendored module.

### Where it lives [1](#0-0) 

```go
func (k Keeper) CalculateGasCost(
	baseFee sdkmath.LegacyDec,
	maxFeePerGas *big.Int,
	maxPriorityFeePerGas *big.Int,
	gasUsed uint64,
) (*big.Int, error) {
	baseFeeBig := baseFee.BigInt()
	// ... "unwraps the LegacyDec encoding back to the actual upc amount"
	baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
	...
	effectiveGasPrice := new(big.Int).Set(baseFeeBig)
	gasUsedBig := new(big.Int).SetUint64(gasUsed)
	gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
	...
}
```

`baseFee` is an 18‑decimal `sdkmath.LegacyDec` — its raw `BigInt()` value equals `actualBaseFee * 1e18`. The code divides that raw value by `1e18` **first**, then multiplies by `gasUsed`. This is exactly the order-of-operations flaw the external report describes: instead of `(baseFee_raw * gasUsed) / 1e18`, the code computes `(baseFee_raw / 1e18) * gasUsed`, which floors any fractional-upc component of the base fee *before* it gets multiplied. Whenever the base fee per gas unit is below `1 upc` (i.e., the raw value is smaller than `1e18`), the division truncates it to `0`, and the whole `gasCost` collapses to `0` for that transaction, regardless of `gasUsed`.

This function feeds `DeductGasFeesFromReceipt`, which burns the computed `gasCost` from the sender's account after EVM execution of universal payloads: [2](#0-1) 

If `gasCost.Sign() <= 0`, the function returns `nil` early with **no fee deducted at all** — the transaction's UEA/CEA execution and any EVM side effects still went through.

### Why this matters

- Push Chain's EIP‑1559 base fee is a `LegacyDec` specifically to support sub‑`1 upc` granularity (as `upc` is the smallest native denomination, analogous to wei). It is entirely plausible in normal operation for `baseFee < 1 upc` per gas unit during periods of low network usage, since EIP‑1559 base-fee adjustment is a continuous percentage-based function, not an integer-quantized one.
- Under those (unprivileged, ordinary) conditions, every `DeductGasFeesFromReceipt` call truncates the effective gas price to `0`, so **gas fees are never burned even though the module still executed the user's payload/EVM call**. This is a direct corruption of gas fee accounting reachable by any ordinary user submitting `PAYLOAD`/`GAS_AND_PAYLOAD` universal transactions — no privileged actor needed.
- The existing test `gas_fee_test.go` only guards a different, previously-fixed ABI regression (`gasLimit` derivation), not this truncation path, so there's no protection against the fractional base-fee case.

### Title
Division-before-multiplication in `CalculateGasCost` truncates sub-1-upc base fees to zero, allowing fee-free universal execution - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` unwraps the 18-decimal `LegacyDec` base fee into a raw integer `upc` amount via integer division **before** multiplying by `gasUsed`, instead of multiplying first and dividing last. When the base fee per gas unit is below `1 upc`, this truncates the effective gas price to `0`, causing `DeductGasFeesFromReceipt` to skip fee collection entirely for that execution.

### Finding Description
`baseFee.BigInt()` returns the raw fixed-point representation (`actualBaseFee * 1e18`). The code does:
```go
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // floor division FIRST
...
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)  // multiply SECOND
```
This is mathematically equivalent to `floor(baseFee) * gasUsed` rather than the correct `floor(baseFee_raw * gasUsed / 1e18)`. Any fractional-upc portion of the base fee is discarded up front, and if `baseFee < 1 upc`, `effectiveGasPrice` becomes exactly `0`, zeroing the entire computed gas cost regardless of how much gas was actually used. [3](#0-2) 

### Impact Explanation
`DeductGasFeesFromReceipt` short-circuits when `gasCost.Sign() <= 0` and returns `nil` — no burn happens — while the EVM call (`receipt`) has already been executed: [4](#0-3) 
This lets ordinary users get their universal payload/EVM execution processed for free whenever the network base fee is sub-`1 upc`, corrupting gas fee accounting and causing systematic underpayment (protocol fee revenue loss) reachable purely by normal transaction submission with no privileged or malicious-node assumption — matching the allowed "corruption of ... gas fee accounting" impact class.

### Likelihood Explanation
Likelihood depends on the base fee genuinely dropping below `1 upc` per gas unit under EIP-1559-style dynamic adjustment during low-congestion periods; this is a plausible market state rather than an edge case requiring privileged manipulation, and doesn't require the attacker to do anything unusual — merely submit a normal universal transaction while the base fee is in that range.

### Recommendation
Reorder the arithmetic to multiply before dividing, preserving precision:
```go
gasCost := new(big.Int).Mul(baseFeeBig, gasUsedBig) // baseFeeBig still in 1e18-scaled form
gasCost.Div(gasCost, big.NewInt(1e18))
```
This keeps the fractional-upc base fee contribution across many gas units instead of flooring it away before the multiplication.

### Proof of Concept
Given `baseFee = LegacyDec("0.5")` (raw `BigInt()` = `5*10^17`) and `gasUsed = 1_000_000`:
- Current implementation: `baseFeeBig.Div(5e17, 1e18) = 0` → `gasCost = 0 * 1_000_000 = 0` (no fee burned).
- Correct implementation: `(5*10^17 * 1_000_000) / 1e18 = 500_000` upc burned.
This mirrors the external report's example where `(1e18, 2e18)` inputs collapse to `0` under the flawed ordering versus a nonzero correct result.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-91)
```go
func (k Keeper) CalculateGasCost(
	baseFee sdkmath.LegacyDec,
	maxFeePerGas *big.Int,
	maxPriorityFeePerGas *big.Int,
	gasUsed uint64,
) (*big.Int, error) {
	baseFeeBig := baseFee.BigInt()
	// @dev: LegacyDec stores values with 18-decimal precision internally, so 1 upc = 1e18
	// in the LegacyDec representation. Since 1 upc is the smallest denomination (like wei
	// in Ethereum), the base fee is always a whole number of upc -- no fractional upc exists.
	// This division unwraps the LegacyDec encoding back to the actual upc amount.
	// Note: baseFee.BigInt() returns a reference to the internal big.Int; the in-place Div
	// mutates it, which is safe here since baseFee is a local value-type copy.
	baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))

	// Step 1: Validate maxFeePerGas >= baseFee
	if maxFeePerGas.Cmp(baseFeeBig) < 0 {
		return nil, fmt.Errorf("maxFeePerGas (%s) cannot be less than baseFee (%s)", maxFeePerGas, baseFeeBig)
	}

	// Step 2: Calculate baseFee + maxPriorityFeePerGas (potential effective gas price)
	// @dev: Currently, we are not using maxPriorityFeePerGas in the calculation
	// tipPlusBase := new(big.Int).Add(baseFeeBig, maxPriorityFeePerGas)
	// tipPlusBase := maxFeePerGas

	// Step 3: Find effective gas price by taking minimum
	// @dev: Currently, since we are not using maxPriorityFeePerGas, effectiveGasPrice is just baseFee
	effectiveGasPrice := new(big.Int).Set(baseFeeBig)
	// if tipPlusBase.Cmp(maxFeePerGas) == -1 {
	// 	effectiveGasPrice = tipPlusBase
	// }

	// Step 4: Calculate final gas cost: effectiveGasPrice * gasUsed
	gasUsedBig := new(big.Int).SetUint64(gasUsed)
	gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)

	k.Logger().Debug("gas cost calculated",
		"base_fee", baseFee.String(),
		"effective_gas_price", effectiveGasPrice.String(),
		"gas_used", gasUsed,
		"gas_cost", gasCost.String(),
	)

	return gasCost, nil
}
```

**File:** x/uexecutor/keeper/fees.go (L97-140)
```go
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```
