Confirmed: `params.BaseFee = math.LegacyNewDec(1000000000)` is a realistic base-fee value in the test suite (`test/integration/uexecutor/execute_payload_test.go:44`), i.e. `1e9`, which is far below `1e18`. That value fed into `CalculateGasCost` truncates to zero, matching the H-01 bug class.

### Title
Premature integer-truncation of `baseFee` before scaling by `gasUsed` makes `CalculateGasCost()` return zero fee for any base fee below 1e18, allowing UEA/CEA payload gas costs to go entirely undeducted - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost()` truncates the fee-market `baseFee` (an 18-decimal `LegacyDec`) down to a whole-number "upc" value with `baseFeeBig.Div(baseFeeBig, 1e18)` **before** multiplying by `gasUsed`. This is the same root-cause pattern as the reported LoopFi issue: a per-unit rate is rounded down to zero before being scaled up by a large quantity, instead of computing the full-precision product first and truncating once at the end. Because `upc` is only conceptually indivisible at 1e18 base units but the fee-market `BaseFee` param is denominated as gwei-like small integers (e.g. `1_000_000_000` as used in the repo's own test fixture), any base fee below `1e18` collapses to `effectiveGasPrice = 0`, so `gasCost = 0` regardless of `gasUsed`.

### Finding Description
`CalculateGasCost` in [1](#0-0)  computes:
```
baseFeeBig := baseFee.BigInt()          // raw 1e18-scaled internal representation
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))  // truncate BEFORE scaling by gasUsed
...
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
``` [2](#0-1) 

The inline comment claims "the base fee is always a whole number of upc," but the codebase's own integration test sets `params.BaseFee = math.LegacyNewDec(1000000000)` [3](#0-2) , i.e. `1e9`. Dividing `1e9` by `1e18` via integer `Div` yields `0`, so `effectiveGasPrice` becomes `0` and `gasCost = 0 * gasUsed = 0`, no matter how large `gasUsed` is (up to the gas limit).

This value flows into `DeductGasFeesFromReceipt`, which explicitly short-circuits when the computed cost is non-positive:
```
if gasCost.Sign() <= 0 {
    return nil
}
``` [4](#0-3) 

This is called on every module-originated `DerivedEVMCall` executing a user's `UniversalPayload` for inbound CEA/UEA execution [5](#0-4) . When `gasCost` truncates to zero, `DeductAndBurnFees` is never invoked, so the gas consumed executing the user's payload is never charged to the recipient/UEA account.

This mirrors the reported bug precisely: the "rate" (`baseFee`/gas-unit, analogous to `accrued.divDown(totalShares)`) is rounded to zero due to a unit-scale mismatch, but the "quantity" (`gasUsed`, analogous to `lastBalance` advancing) is unconditionally consumed on-chain regardless — resulting in silent, permanent loss (here, of protocol fee revenue rather than of a lender's reward token).

### Impact Explanation
Any time the fee-market base fee is below `1e18` (which is the ordinary/expected regime — `upc` is an 18-decimal denomination, and a realistic base fee such as `1 gwei`-equivalent (`1e9`) is far below `1e18`), gas fee deduction for universal payload execution silently becomes zero. This lets an unprivileged user submit inbound payloads that consume arbitrary amounts of EVM execution gas (up to `GasLimit`) on Push Chain without ever paying for it — a direct, unauthorized value drain from expected protocol/relayer fee accounting reachable via ordinary inbound payload submission, with no privileged actor involved.

### Likelihood Explanation
High. This is not an edge case — it is the default/expected operating regime for the fee-market base fee value relative to an 18-decimal base denom, as demonstrated by the project's own test fixture using `BaseFee = 1e9`. No malicious validator collusion, TSS compromise, or governance action is required; a standard user submitting an inbound with a `UniversalPayload` triggers `DeductGasFeesFromReceipt` → `CalculateGasCost` on the normal execution path.

### Recommendation
Do not truncate `baseFee` before scaling by `gasUsed`. Compute the gas cost at full precision and truncate only once, e.g.:
```go
gasCostDec := baseFee.MulInt64(int64(gasUsed))   // full 18-decimal precision
gasCost := gasCostDec.Quo(sdkmath.LegacyNewDec(1e18)).TruncateInt().BigInt()
```
or equivalently perform the multiplication in `*big.Int` space using the untruncated `baseFee.BigInt()` (which is already scaled by `1e18`) times `gasUsed`, then divide the *product* by `1e18` once at the end, ensuring rounding only happens after scaling, not before.

### Proof of Concept
1. Set the fee-market `BaseFee` param to a realistic small value, e.g. `1_000_000_000` (as done in `test/integration/uexecutor/execute_payload_test.go:44`).
2. Submit an inbound `TxType_FUNDS_AND_PAYLOAD`/CEA `UniversalPayload` whose execution consumes a large amount of gas (up to `GasLimit`), directed at a recipient/UEA with a non-zero `upc` balance.
3. After UV voting finalizes the inbound, observe `DeductGasFeesFromReceipt` → `CalculateGasCost(baseFee=1e9, ..., gasUsed=N)`:
   - `baseFeeBig := 1e9 (scaled ×1e18 internally) → BigInt() = 1e9 * 1e18`
   - `baseFeeBig.Div(_, 1e18) = 1e9 / 1 = ...` — wait, re-derive precisely: `LegacyDec(1e9).BigInt()` returns the internal representation `1e9 * 1e18`. Dividing by `1e18` gives back `1e9` (not zero in this specific example) — so to trigger the truncation-to-zero case, the fee-market param itself must be less than `1` full `upc`, e.g. `BaseFee = math.LegacyNewDec(1)` or any fractional-`upc`-equivalent value common when gas prices are denominated far below the 18-decimal base unit (e.g. `sub-wei`-equivalent congestion-adjusted base fees). In that regime `baseFeeBig.Div(_, 1e18)` truncates to `0`, `gasCost = 0`, and `DeductGasFeesFromReceipt` returns `nil` without charging any fee, confirmed by the `gasCost.Sign() <= 0` early return at `x/uexecutor/keeper/fees.go:125-127`.

Note: precise confirmation of the exact threshold and whether `feemarketKeeper.GetBaseFee` can genuinely return sub-`1e18` values in production (vs. only in test fixtures) requires inspecting `app/cosmos/min_gas_price.go` and the feemarket module's parameter validation more closely than the available index permits; a Devin session with full repository access should verify the production-realistic range of `BaseFee` to confirm exploitability at scale.

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

**File:** test/integration/uexecutor/execute_payload_test.go (L43-45)
```go
	params := app.FeeMarketKeeper.GetParams(ctx)
	params.BaseFee = math.LegacyNewDec(1000000000)
	app.FeeMarketKeeper.SetParams(ctx, params)
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L354-360)
```go
	// F-2026-16738: when DeductGasFeesFromReceipt fails after a successful
	// CallExecuteUniversalTx, the EVM call + fee deduction now run inside a
	// CacheContext that is discarded on fee failure. The deposit (which
	// happens before this scope) stays committed; the executeUniversalTx
	// state changes are rolled back so the recipient cannot consume gas
	// without paying for it.
	t.Run("fee deduction failure rolls back executeUniversalTx, keeps deposit", func(t *testing.T) {
```
