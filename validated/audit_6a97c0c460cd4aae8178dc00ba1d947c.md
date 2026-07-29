## Analog Found

<title>Integer division before multiplication in `CalculateGasCost` can silently zero out EVM gas fees for UEA-executed payloads - (File: x/uexecutor/keeper/fees.go)</title>

### Summary
The Surge report's root cause is a division-before-multiplication pattern (`_totalDebt * _borrowRate * _timeDelta / (365 days * 1e18)`) that truncates to zero for small-enough operands, silently zeroing accrued interest. Push Chain's `CalculateGasCost` in `x/uexecutor/keeper/fees.go` has the same structural flaw: it performs an integer division on `baseFee` *before* multiplying by `gasUsed`, which can truncate the effective gas price to zero and cause `DeductGasFeesFromReceipt` to silently skip fee collection for real, metered EVM execution.

### Finding Description
`CalculateGasCost` converts the `LegacyDec` base fee to a raw `upc` integer by dividing by `1e18` *before* it is used as a price multiplier: [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // <-- integer division FIRST
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)  // <-- multiplication AFTER truncation
```

The inline comment asserts this is safe because "1 upc is the smallest denomination... the base fee is always a whole number of upc." That assumption is not enforced anywhere in this code path — it depends entirely on the external `feemarketKeeper.GetBaseFee` (EIP-1559-style base fee) always returning an exact multiple of `1e18` in its `LegacyDec` representation. The EIP-1559 base-fee adjustment algorithm recomputes the base fee every block as a fractional percentage change of the previous value based on gas-used vs. gas-target, and nothing in the reviewed code path clamps or rounds that result up to whole-`upc` (`1e18`) multiples before it reaches `CalculateGasCost`. If `GetBaseFee` ever returns a `LegacyDec` whose raw big.Int representation is less than `1e18` (i.e. an actual base fee below 1 `upc`, the smallest token unit), `baseFeeBig.Div(baseFeeBig, 1e18)` truncates to `0`.

This flows directly into the caller: [2](#0-1) 

`gasCost.Sign() <= 0` causes `DeductGasFeesFromReceipt` to return `nil` (no-op), and no fee is ever deducted or burned via `DeductAndBurnFees`, even though the receipt shows nonzero real gas consumed by a `DerivedEVMCall`-executed universal payload (`ExecutePayloadV2` in `x/uexecutor/keeper/execute_payload.go`).

### Impact Explanation
This corrupts gas fee accounting — an impact explicitly listed as in-scope ("corruption of ... gas fee accounting"). Whenever the effective base fee (as reported by the fee market) drops below one whole `upc` unit, every `MsgExecutePayload` executed by ordinary unprivileged users pays **zero** gas fee for real EVM execution, regardless of how much gas is actually consumed. This is a systemic underpayment/fee-bypass condition reachable purely from normal, honest user transaction submission (no privileged or validator collusion needed), and it degrades protocol revenue/fee accounting invariants across the universal-execution gas billing path.

### Likelihood Explanation
Triggering requires the fee market's base fee to fall below `1e18` (1 `upc`) in its internal representation — plausible under sustained low network utilization when the base-fee-adjustment mechanism drives price down, or if `MinGasPrice`/`base_fee` params are configured to small fractional values. The code contains no assertion, floor, or rounding-up safeguard defending the "always a whole upc" assumption stated in the comment; it is purely incidental on feemarket configuration and adjustment dynamics, not a guaranteed invariant of this repository's own code.

### Recommendation
- Do not divide `baseFee` by `1e18` before multiplying by `gasUsed`. Multiply first, then divide once at the end (mirroring the Surge fix recommendation of preserving precision through the full calculation), e.g. compute `gasCost = baseFee.BigInt() * gasUsed / 1e18` in a single combined operation, or keep the entire computation in `LegacyDec` domain and only truncate to an integer `upc` amount at the very end.
- Add an explicit guard: if the computed `gasCost` rounds to zero while `gasUsed > 0` and `baseFee > 0`, treat this as an error condition (fail-safe) rather than silently skipping fee collection, similar to the Surge recommendation to not silently accept a zero-interest/zero-fee outcome from a lossy division.

### Proof of Concept
1. Feemarket base fee (via EIP-1559-style adjustment or governance/min-gas-price configuration) settles to a `LegacyDec` value below `1e18` (e.g., representing `0.5 upc`), returned by `feemarketKeeper.GetBaseFee`.
2. A user submits `MsgExecutePayload`, which is routed through `ExecutePayloadV2` → `CallUEAExecutePayload` (a real `DerivedEVMCall`, gas metered, receipt shows `GasUsed > 0`).
3. `DeductGasFeesFromReceipt` calls `CalculateGasCost(baseFee, ...)`; `baseFeeBig.Div(baseFeeBig, 1e18)` truncates `0.5e18 / 1e18` to `0`.
4. `effectiveGasPrice = 0`, `gasCost = 0 * gasUsed = 0`.
5. `gasCost.Sign() <= 0` short-circuits `DeductGasFeesFromReceipt` to a no-op — no fee burned from the user despite real gas consumption recorded in the receipt.

### Citations

**File:** x/uexecutor/keeper/fees.go (L52-81)
```go
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

**File:** x/uexecutor/keeper/fees.go (L116-127)
```go
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
```
