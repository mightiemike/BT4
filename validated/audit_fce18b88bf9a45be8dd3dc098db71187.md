### Title
`CalculateGasCost` truncates a sub-1-upc `baseFee` to zero, making PRC20-execution EVM gas fees permanently uncharged - ([File: x/uexecutor/keeper/fees.go])

### Summary
`x/uexecutor/keeper/fees.go`'s `CalculateGasCost` unwraps the fee-market `baseFee` (an `sdkmath.LegacyDec` with 18-decimal fixed-point precision) into a whole-`upc` `big.Int` by doing `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` [1](#0-0) . This is the same class of bug as the reported `SLICE_PERIOD` issue: any value below the divisor floors to zero via integer division, and the code's own comment assumes an invariant ("base fee is always a whole number of upc") that is not actually enforced anywhere in the fee-market parameterization.

### Finding Description
`DeductGasFeesFromReceipt` fetches the live base fee via `k.feemarketKeeper.GetBaseFee(sdkCtx)` and feeds it into `CalculateGasCost` [2](#0-1) . The `feemarket` module (Cosmos EVM's dynamic base-fee module) adjusts `BaseFee` block-by-block using an EIP-1559-style multiplicative formula based on gas utilization, which can legitimately produce fractional `LegacyDec` values that are not exact multiples of `1e18` (e.g., `0.87 upc`, `0.5 upc`) — nothing in the scoped code forces `BaseFee` to remain an integer number of `upc`. The `Div` call in `CalculateGasCost` truncates such sub-1-upc values straight to `0`, exactly mirroring the vesting bug's `(timeFromStart / SLICE_PERIOD) * SLICE_PERIOD` rounding-to-zero pattern.

When `effectiveGasPrice` becomes `0`, `gasCost = effectiveGasPrice * gasUsed = 0` regardless of how much gas the EVM call (payload execution, PRC20 deposit/swap, refund, etc.) actually consumed. `DeductGasFeesFromReceipt` explicitly treats `gasCost.Sign() <= 0` as a no-op and returns without charging anything [3](#0-2) . This is reachable purely by ordinary users submitting `MsgExecutePayload`/inbound-execution transactions while the fee market happens to be at a sub-1-upc base fee — no privileged actor is required.

### Impact Explanation
This corrupts gas-fee accounting in the universal execution path: `UNIVERSAL_CORE`/`DerivedEVMCall`-driven executions are metered and settled by `x/uexecutor`, and this rounding bug causes gas fees to be silently and deterministically zeroed for every execution while `baseFee` is below `1 upc`, i.e., the protocol under-collects (and can indefinitely fail to collect) fees for potentially large amounts of consumed EVM gas. This falls under the allowed impact "corruption of ... gas fee accounting" and is the same underlying invariant break as the reported analog (integer-truncation-to-zero of a legitimate accrual/charge quantity), though here the practical effect is fee-collection loss for the protocol rather than user-fund lockup.

### Likelihood Explanation
Medium: it requires the live `BaseFee` value to fall below `1e18` (1 whole `upc`) at execution time, which depends on genesis/params configuration and the feemarket's dynamic adjustment behavior over time (the repo's genesis defaults set `base_fee`/`min_gas_price` to large integer values like `1000000000.0`, so on default configs this triggers only if the base fee is driven down over many low-utilization blocks). No attacker privilege is needed to trigger it — any ordinary payload execution during a low base-fee period reaches this code path.

### Recommendation
Do not floor-divide the `LegacyDec` base fee by `1e18` before validation/multiplication. Instead, keep `baseFee` as a `LegacyDec`, perform the `maxFeePerGas`/`gasUsed` comparison and multiplication in `LegacyDec` (or scaled `big.Int` with an explicit round-up policy), and only convert to a whole-`upc` integer at the very end using `Ceil()`/`RoundInt()` (mirroring the `Ceil().RoundInt()` pattern already used correctly in `app/cosmos/min_gas_price.go`). This ensures sub-1-upc base fees still produce a non-zero (rounded-up) charge instead of silently truncating to zero.

### Proof of Concept
1. Let the fee-market base fee decay (via normal EIP-1559 low-utilization adjustment) to a `LegacyDec` value below `1e18`, e.g. `0.9` upc.
2. A user submits an inbound execution (`MsgExecutePayload` or PRC20 auto-swap) that consumes non-trivial EVM gas (e.g., `gasUsed = 500_000`).
3. `DeductGasFeesFromReceipt` calls `CalculateGasCost(baseFee=0.9, ...)`; `baseFeeBig.Div(baseFeeBig, 1e18)` yields `0`.
4. `effectiveGasPrice = 0`, so `gasCost = 0 * 500_000 = 0`.
5. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without calling `DeductAndBurnFees` — the execution's gas is never charged to the recipient, even though real EVM gas was consumed.

### Citations

**File:** x/uexecutor/keeper/fees.go (L53-60)
```go
	baseFeeBig := baseFee.BigInt()
	// @dev: LegacyDec stores values with 18-decimal precision internally, so 1 upc = 1e18
	// in the LegacyDec representation. Since 1 upc is the smallest denomination (like wei
	// in Ethereum), the base fee is always a whole number of upc -- no fractional upc exists.
	// This division unwraps the LegacyDec encoding back to the actual upc amount.
	// Note: baseFee.BigInt() returns a reference to the internal big.Int; the in-place Div
	// mutates it, which is safe here since baseFee is a local value-type copy.
	baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
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
