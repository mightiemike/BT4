No dedicated unit test exists for `CalculateGasCost`, confirming this edge case (fractional base fee) is untested. I have enough evidence to present the finding.

### Title
Gas fee truncated to zero when base fee is sub-1-upc, letting universal transactions execute for free — ([File: x/uexecutor/keeper/fees.go])

### Summary
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` divides the fee-market base fee (a `LegacyDec` with 18-decimal fixed-point encoding) by `1e18` *before* multiplying by `gasUsed`, exactly mirroring the reported `EmergencyWithdraw` bug pattern of "divide, then multiply." Because the division happens first, any base fee smaller than one whole `upc` is truncated to `0` before the multiplication by `gasUsed` ever occurs, producing a gas cost of exactly `0` for the entire transaction regardless of how much gas was actually used.

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

then later: [2](#0-1) 

The code assumes the fee-market's `baseFee` is always an exact integer multiple of `1e18` in its `LegacyDec` raw representation (i.e., a whole number of `upc`). This assumption is not enforced anywhere in this file — `baseFee` comes from `k.feemarketKeeper.GetBaseFee(sdkCtx)` [3](#0-2) , an EIP-1559-style dynamic value computed elsewhere via ratio-based `Dec` arithmetic (`baseFee * gasUsedDelta / targetGas / denominator`), which can legitimately settle on values with a fractional component (e.g., `0.5 upc` per gas unit) whenever the configured/adjusted base fee is small — plausible at genesis, after a governance-driven reduction of `MinGasPrice`, or during sustained low network utilization where the EIP-1559 base fee decays toward small values.

When `baseFee < 1` (raw big.Int representation `< 1e18`), `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates it to `0` *before* the multiplication by `gasUsedBig` in `gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)`. Since `0 * gasUsed == 0` for any `gasUsed`, the resulting `gasCost` is always `0`, no matter how much gas the universal transaction actually consumed.

`DeductGasFeesFromReceipt` then short-circuits on this: [4](#0-3)  — `gasCost.Sign() <= 0` causes an early `return nil`, meaning `DeductAndBurnFees` (which transfers-then-burns the fee from the UEA's account) is never invoked. This is the exact analog of the reported bug: the code divides by the fixed-point denominator first, then multiplies, causing the intermediate value to round to an all-zero result and destroying precision that the correct order (`multiply then divide/round`) would have preserved.

### Impact Explanation
This corrupts gas fee accounting for the universal execution flow (`x/uexecutor`), which is explicitly a required in-scope impact ("corruption of ... gas fee accounting"). Any ordinary user submitting a `UniversalTx`/inbound payload during a period when the network's dynamic base fee is below one `upc` per gas unit gets EVM execution for effectively free, since no burn happens on their behalf. This lets an unprivileged attacker cheaply spam expensive `DerivedEVMCall` executions with no fee cost to the protocol's intended anti-spam/fee-burn mechanism, purely by waiting for or inducing low-base-fee conditions — no privileged access, malicious validator, or governance abuse required.

### Likelihood Explanation
Triggering requires only that the fee-market's dynamic base fee fall below `1` raw `upc` unit — a state reachable under normal EIP-1559-style base-fee decay during low chain utilization, or immediately after chain launch/param changes that set a small base fee. No attacker-controlled input is needed beyond simply submitting transactions when this condition holds, making this a passive, reliably-triggerable accounting defect rather than a contrived edge case.

### Recommendation
Reorder the computation to multiply before dividing, preserving fractional precision until the final truncation to an integer number of `upc`, e.g. compute `gasCost = baseFee.MulInt64(int64(gasUsed)).TruncateInt().BigInt()` (or equivalent `Dec`-based multiply-then-round) instead of truncating `baseFee` to an integer before multiplying by `gasUsed`.

### Proof of Concept
1. Let the fee-market base fee be `0.5 upc` (a legitimate `LegacyDec` value, raw internal representation `5*10^17`).
2. A user submits a universal transaction whose EVM execution consumes `gasUsed = 1,000,000`.
3. In `CalculateGasCost`: `baseFeeBig.Div(5e17, 1e18) == 0` (integer division truncates).
4. `gasCost = 0 * 1,000,000 = 0`.
5. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without deducting or burning any fee, despite `1,000,000` gas units of real EVM execution having occurred — the correct fee (`0.5 * 1,000,000 = 500,000 upc`) is silently never charged.

### Citations

**File:** x/uexecutor/keeper/fees.go (L52-60)
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
```

**File:** x/uexecutor/keeper/fees.go (L74-81)
```go
	effectiveGasPrice := new(big.Int).Set(baseFeeBig)
	// if tipPlusBase.Cmp(maxFeePerGas) == -1 {
	// 	effectiveGasPrice = tipPlusBase
	// }

	// Step 4: Calculate final gas cost: effectiveGasPrice * gasUsed
	gasUsedBig := new(big.Int).SetUint64(gasUsed)
	gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
```

**File:** x/uexecutor/keeper/fees.go (L116-116)
```go
	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
```

**File:** x/uexecutor/keeper/fees.go (L121-127)
```go
	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}
```
