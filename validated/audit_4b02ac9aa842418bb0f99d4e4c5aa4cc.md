## Analysis

The external report's core defect pattern is a **division that rounds down when computing a per-unit price/fee, causing systemic under-collection relative to the intended target amount**. The Push Chain analog lives in the gas-fee accounting path for universal payload execution.

### Title
Gas fee under-collection from integer truncation of dynamic base fee in `CalculateGasCost` — (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` converts the feemarket's `LegacyDec` base fee (18-decimal fixed point) into a whole `upc` integer by doing `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))`, based on the embedded assumption that "the base fee is always a whole number of upc." That assumption does not hold for the feemarket module's dynamic EIP-1559-style base fee, which is adjusted every block as a function of gas usage relative to a target and is a `LegacyDec` value that is not constrained to be an exact multiple of `1e18`. Any fractional remainder is silently truncated (floor division), permanently discarding the sub-unit portion of the effective gas price before it is multiplied by `gasUsed`.

### Finding Description [1](#0-0) 

`CalculateGasCost` is invoked from `DeductGasFeesFromReceipt` [2](#0-1)  which is reached from ordinary, unprivileged, user-triggered flows such as `execute_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`, and `msg_execute_payload.go` — i.e., every time a `UniversalPayload` (CEA/UEA execution) actually runs EVM code and gas is consumed, the module computes `gasCost = floor(baseFee/1e18) * gasUsed` and burns exactly that amount from the recipient's balance via `DeductAndBurnFees` [3](#0-2) .

Because `baseFeeBig.Div(...)` uses Go's `big.Int` truncating division, any fractional-upc component of the true base fee (e.g. `1_000_000_000.37...` upc, which is exactly what an EIP-1559-style adjustment formula produces when it scales the previous base fee by a gas-usage ratio) is dropped to `1_000_000_000`. Over many payload executions this systematically under-collects the fee that the protocol's own fee market determined was owed, mirroring the `minBidPrices[currentId] = _initialPrice / _totalSupply` rounding-down defect in the report: the "unit price" used for the actual charge is silently lower than the value the mechanism intended, and the shortfall accumulates across every execution rather than being an isolated off-by-one.

### Impact Explanation
This corrupts native gas-fee accounting for the universal execution path (explicitly listed in-scope: "corruption of ... gas fee accounting ... [or] canonical UniversalTx state"). It is triggerable by any unprivileged user simply by executing a payload through the normal CEA/UEA flow while network congestion (or any base-fee movement) makes the current base fee non-integral in upc terms — no privileged actor, validator collusion, or malicious peer assumption is required. The result is a protocol-wide, block-by-block underpayment of gas fees relative to what the fee market computed, functionally identical in class to the referenced GroupBuy bug: the accounting mechanism looks correct but the amount actually collected is less than the amount the protocol's own pricing logic determined should be collected.

### Likelihood Explanation
High: the dynamic base fee is recalculated every block as a function of gas usage vs. target (an EIP-1559-style multiplicative adjustment), and there is no mechanism forcing that adjusted value to remain an exact multiple of `1e18` upc — it is stored as an 18-decimal `LegacyDec`, and the surrounding code explicitly acknowledges (in its own comment) an assumption of integral-only values without enforcing or verifying it. Every payload execution that consumes gas is affected whenever the base fee is fractional, which is the normal, expected steady state of any EIP-1559 congestion-pricing base fee rather than an edge case.

### Recommendation
Round the effective gas price (and thus the derived gas cost) **up** rather than truncating down when converting the `LegacyDec` base fee to an integer `upc` amount — mirroring the report's own recommended fix of rounding `minBidPrices` up. Concretely, replace the floor `Div` with a ceiling conversion (e.g. `baseFee.Ceil().RoundInt()` equivalent semantics, or compute `(baseFeeBig + 1e18 - 1) / 1e18`) so the amount actually burned from the recipient always covers at least the fee-market-determined cost, and add an explicit invariant check/log if a non-integral base fee is ever observed instead of silently assuming it away.

### Proof of Concept
1. Let the feemarket compute (via its normal per-block adjustment, no admin action) a base fee of `1_000_000_000_500_000_000_000_000_000` in `LegacyDec` internal representation — i.e., `1,000,000,000.5` upc.
2. A user submits any transaction that triggers `DeductGasFeesFromReceipt` (e.g., a CEA/UEA payload execution) with `gasUsed = 100_000`.
3. `CalculateGasCost` computes `baseFeeBig.Div(baseFeeBig, 1e18)` → `1,000,000,000` (the `.5` truncated), then `gasCost = 1,000,000,000 * 100,000 = 100,000,000,000,000 upc`.
4. The mathematically correct fee market cost was `1,000,000,000.5 * 100,000 = 100,000,000,050,000 upc`.
5. `DeductAndBurnFees` burns only the truncated amount, permanently under-collecting `50,000 upc` for this single execution — repeated across every execution in the network, this compounds into systemic fee-accounting shortfall with no path to recovery. [4](#0-3)

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

**File:** x/uexecutor/keeper/fees.go (L47-66)
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

```

**File:** x/uexecutor/keeper/fees.go (L116-124)
```go
	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
```
