### Title
Division-before-multiplication in `CalculateGasCost` truncates the base fee before scaling by gas used, allowing systemic under-charging of UEA payload gas fees - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` computes the gas fee charged to a Universal Executor Account (UEA) for `DerivedEVMCall`/payload execution by first dividing `baseFee`'s internal `LegacyDec` representation by `1e18` and only then multiplying the result by `gasUsed`. This is the same order-of-operations flaw as the GMX `feeSplit` finding: dividing before multiplying truncates any fractional-upc component of the base fee, and that lost fraction is effectively multiplied away across the entire `gasUsed` amount rather than being preserved and applied per-unit.

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... the base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasUsedBig := new(big.Int).SetUint64(gasUsed)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
```

The comment asserts the invariant that `baseFee` (an `sdkmath.LegacyDec` obtained from `k.feemarketKeeper.GetBaseFee(sdkCtx)`) is always an exact whole-number multiple of `1e18` in its internal representation, so the early `Div` is claimed to be lossless. That base fee, however, is produced and continuously adjusted by the external EIP‑1559‑style feemarket module (`k.feemarketKeeper.GetBaseFee`, called from `DeductGasFeesFromReceipt` at `x/uexecutor/keeper/fees.go:116`), whose dynamic adjustment formula operates on `LegacyDec` (18-decimal fixed point) values and multiplies/divides by ratios such as gas-used/gas-target and a change denominator. Nothing in the code shown in this repository enforces or asserts that the resulting `baseFee` is always an exact multiple of `1e18` (only genesis/test configs happen to hard-code whole numbers, e.g. `"1000000.000000000000000000"` in genesis scripts and `sdkmath.NewInt(1000000000000000000)` in test setup). If the live-adjusted base fee ever contains a fractional-upc component, `baseFeeBig.Div(baseFeeBig, 1e18)` silently truncates that fraction to zero **before** the `Mul` by `gasUsedBig` happens, so the truncated fraction is lost for the *entire* transaction's gas usage instead of being rounded once at the end. This is the identical bug class as the GMX report: doing `Div` (truncating) ahead of `Mul` amplifies/loses precision that a `Mul`-then-`Div` ordering would have preserved.

`CalculateGasCost`'s result feeds directly into `k.DeductAndBurnFees` ( [2](#0-1) ), which burns exactly `gasCost` upc from the payer's balance — this is squarely in the "gas fee accounting" / "refund accounting" impact category for the Universal Execution flow (`ExecuteInboundGasAndPayload` → `DeductGasFeesFromReceipt` → `CalculateGasCost` → `DeductAndBurnFees`).

### Impact Explanation
If the feemarket's dynamically-adjusted base fee is not an exact multiple of `1e18` at some block, every UEA payload execution at that block under-charges gas fees by up to `(fractional part of baseFee) × gasUsed` upc, which for a full-block-size gas usage can be a non-trivial amount of protocol revenue. This corrupts gas fee accounting in a way an unprivileged user does not need any special privilege to trigger — it happens automatically on every ordinary transaction that hits `DeductGasFeesFromReceipt` while the base fee carries a fractional-upc remainder. This maps to the allowed impact "corruption of ... gas fee accounting" reachable "from ordinary user deposits, payloads, contracts, or default transaction submission paths alone."

### Likelihood Explanation
Likelihood hinges entirely on whether the feemarket module's base-fee adjustment can ever produce a `LegacyDec` value whose internal representation is not an exact multiple of `1e18` — i.e., a genuinely fractional upc base fee. That computation lives in the external `cosmos/evm` `x/feemarket` dependency, not in this repository, and I could not locate or inspect its `CalculateBaseFee`/adjustment logic within the scanned codebase to confirm or rule out fractional outputs. The comment in `fees.go` explicitly assumes this can never happen, but no assertion or guard enforces it, and typical EIP-1559 base-fee adjustment formulas (percentage-based deltas) are not inherently guaranteed to land on multiples of `1e18` after arbitrary numbers of blocks of adjustment. Given this uncertainty over the external dependency's exact arithmetic, I cannot definitively confirm the fractional-baseFee precondition is reachable in production — this is a real code smell and same bug class as the referenced report, but without verifying the feemarket adjustment formula's output domain, the practical exploitability/materiality is uncertain.

### Recommendation
Perform the multiplication before the division: compute `gasCost = baseFeeBig(unscaled, ×1e18) .Mul(gasUsedBig)`, then divide the product by `1e18` once at the end (mirroring the GMX report's fix of "perform division at the end of the calculation"). Alternatively, keep the entire computation in `LegacyDec` (`baseFee.MulInt64(int64(gasUsed))`) and only truncate to an integer `upc` amount as the very last step, removing the assumption that `baseFee` is always a whole upc number.

### Proof of Concept
Could not be fully constructed/verified within this session because the root numeric input (whether `feemarketKeeper.GetBaseFee` can return a `LegacyDec` with non-zero digits below the 18th decimal place) is produced by an external dependency (`cosmos/evm x/feemarket`) whose source was not available in the indexed codebase to inspect directly. A concrete PoC would be:
1. Drive the feemarket's base-fee adjustment (via block gas usage above/below target) over several blocks until `GetBaseFee(ctx)` returns a `LegacyDec` whose stored value is not an exact multiple of `1e18` (e.g. `1_000_000_000_000_000_001` wei-equivalent i.e. `1.000000000000000001` upc).
2. Call `DeductGasFeesFromReceipt` (via a normal UEA payload transaction) with a large `gasUsed` (e.g. `21_000_000`, the max seen in test payloads).
3. Compare the burned amount from `DeductAndBurnFees` against the mathematically correct `baseFee_Dec.MulInt64(gasUsed).TruncateInt()`; the actual burned amount will be `gasUsed` upc lower per unit of truncated fraction (i.e., undercharged by `(fractional_baseFee) × gasUsed`).

This aspect (confirming the feemarket module can produce non-integer-upc base fees) is unverified from the indexed code and should be validated by a background agent with full source access to the `cosmos/evm` `x/feemarket` dependency before treating this as a confirmed, exploitable finding rather than a defensive-coding gap.

### Citations

**File:** x/uexecutor/keeper/fees.go (L21-37)
```go
func (k Keeper) DeductAndBurnFees(ctx context.Context, from sdk.AccAddress, gasCost *big.Int) error {
	amt := sdkmath.NewIntFromBigInt(gasCost)
	coin := sdk.NewCoin(pchaintypes.BaseDenom, amt)

	k.Logger().Debug("deducting and burning fees",
		"from", from.String(),
		"gas_cost", gasCost.String(),
		"denom", pchaintypes.BaseDenom,
	)

	err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, from, types.ModuleName, sdk.NewCoins(coin))
	if err != nil {
		return err
	}

	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(coin))
}
```

**File:** x/uexecutor/keeper/fees.go (L47-81)
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
```
