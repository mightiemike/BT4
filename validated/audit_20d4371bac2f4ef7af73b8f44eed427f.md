## Finding: Precision-loss truncation in `CalculateGasCost` undercharges universal-payload gas fees

The div-before-mul pattern from the Abracadabra `_GeneralIntegrate()` finding has a direct analog in `x/uexecutor/keeper/fees.go`.

### Title
Division-before-multiplication truncates fractional base fee, systematically undercharging universal-payload gas fees — (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` unwraps the EIP-1559 `baseFee` (an `sdkmath.LegacyDec` with 18-decimal internal precision) by dividing its raw big.Int representation by `1e18` **before** multiplying by `gasUsed`. This discards any fractional-upc component of the base fee prior to scaling, instead of multiplying first and dividing once at the end — the exact same rounding-direction bug class as `_GeneralIntegrate()`.

### Finding Description [1](#0-0) 

`baseFee.BigInt()` returns the LegacyDec's internal fixed-point value scaled by `1e18`. The code does:

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // truncates fractional upc, rounds DOWN
...
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)  // multiply happens AFTER truncation
``` [2](#0-1) 

The in-code comment asserts "the base fee is always a whole number of upc -- no fractional upc exists," but this is only true if the feemarket's EIP-1559-style base-fee adjustment formula always lands exactly on integer-upc boundaries. That formula (percentage-based increase/decrease driven by block gas usage relative to target) is computed in `LegacyDec` precisely because it does **not** generally produce round numbers — any block where actual gas usage isn't exactly at the gas target moves `baseFee` by a fractional amount, and successive blocks compound this drift. This is triggered purely by ordinary user transaction volume (an unprivileged, always-reachable condition), not by any special attacker action.

Because the division happens before the multiplication by `gasUsed`, the truncated fractional part `f` (0 ≤ f < 1 upc) is lost and effectively multiplied by `gasUsed` implicitly — i.e., the protocol under-collects `f * gasUsed` upc on every gas deduction in `DeductGasFeesFromReceipt`, which is invoked for every universal-payload execution. [3](#0-2) 

### Impact Explanation
Every universal-payload gas deduction (`DeductGasFeesFromReceipt` → `CalculateGasCost` → `DeductAndBurnFees`) undercharges the recipient by up to `gasUsed - 1` upc whenever the block's base fee is not an exact multiple of `1e18` in its Dec representation. This is a deterministic (all-validators-agree) but systematic under-collection of protocol fee revenue — the gas-fee accounting invariant ("charge exactly `effectiveGasPrice * gasUsed`") is violated in a rounding-down direction that always favors the payer and drains protocol-intended fee burn over time. With large `gasUsed` values (e.g., complex payload executions), the per-transaction loss can be substantial and compounds across every transaction on the chain.

### Likelihood Explanation
High reachability: this code path runs on the default, unprivileged transaction flow for every universal payload execution with `GasUsed > 0`, and the triggering condition (base fee drifting off an integer-upc value) is the normal, expected behavior of any EIP-1559-style fee market under real traffic — not a contrived edge case.

### Recommendation
Multiply before dividing: compute `gasCost = (baseFeeBig_full_precision * gasUsedBig) / 1e18` in a single step at the end, rather than truncating `baseFeeBig` to whole upc first. E.g.:

```go
baseFeeBig := baseFee.BigInt() // still scaled by 1e18
...
gasCostScaled := new(big.Int).Mul(baseFeeBig, gasUsedBig)
gasCost := new(big.Int).Quo(gasCostScaled, big.NewInt(1e18))
```
This preserves the fractional-upc contribution across the multiplication instead of discarding it beforehand.

### Proof of Concept
Assume `baseFee` = `1_000_000_000.7` upc (LegacyDec internal value `1000000000700000000000000000` i.e. `baseFeeBig` before any division), and `gasUsed = 3_000_000`.

- Current code: `baseFeeBig.Div(_, 1e18)` → `1_000_000_000` (fractional `.7` truncated); `gasCost = 1_000_000_000 * 3_000_000 = 3_000_000_000_000_000`.
- Correct (multiply-then-divide): `gasCostScaled = 1000000000700000000000000000 * 3_000_000`; `gasCost = gasCostScaled / 1e18 = 3_000_000_002_100_000` (approx, accounting for the `.7` fraction × 3,000,000 ≈ 2,100,000 extra upc).

The difference (~2,100,000 upc per transaction in this example) is silently lost from fee accounting on every such transaction, scaling with `gasUsed` and the magnitude of the base fee's fractional remainder.

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
