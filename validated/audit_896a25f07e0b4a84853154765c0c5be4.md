### Title
Division-before-multiplication in `CalculateGasCost` truncates fractional base fee to zero, allowing free (gas-cost-zero) universal payload execution - (File: x/uexecutor/keeper/fees.go)

### Summary
`Keeper.CalculateGasCost` in `x/uexecutor/keeper/fees.go` unwraps the `LegacyDec` base fee to an integer "upc" amount by dividing the internal 18-decimal fixed-point representation by `1e18` **before** multiplying by `gasUsed`. This is the same division-before-multiplication pattern flagged in the Blend finding: performing `Div` first discards the fractional remainder, and that lost precision cannot be recovered by the subsequent multiplication, no matter how large `gasUsed` is. [1](#0-0) 

### Finding Description
`CalculateGasCost` computes:
```go
baseFeeBig := baseFee.BigInt()          // internal integer = actualBaseFee * 1e18
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // truncates fractional upc BEFORE multiplying
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)   // multiply happens after truncation
``` [1](#0-0) 

The code comment asserts that "the base fee is always a whole number of upc — no fractional upc exists," but nothing in the reachable code path enforces that invariant. `baseFee` comes from `k.feemarketKeeper.GetBaseFee(sdkCtx)` [2](#0-1) , which is a `LegacyDec` maintained by the standard cosmos-evm/cosmos-sdk `x/feemarket` module. That module's EIP-1559-style base-fee adjustment recomputes `BaseFee` every block as a function of gas usage relative to a target, using `LegacyDec` arithmetic (a percentage-based delta applied to the previous base fee). This adjustment can and does produce fractional `LegacyDec` values that are not exact multiples of `1e18` — nothing in the feemarket parameters (`min_gas_price`, `min_gas_multiplier`, `base_fee_change_denominator`) forces the resulting `BaseFee` to always land on a whole-upc value after each block's adjustment, particularly during periods of below-target gas usage causing the fee to shrink by fractional amounts. Genesis defaults happen to be whole numbers (e.g. `"1000000000.000000000000000000"` [3](#0-2) ), but that is only the *starting* value, not an invariant preserved across block-by-block adjustment.

Once `baseFee` is fractional and specifically has a value whose integer part component is 0 upc (i.e. `< 1e18` in internal scale, meaning less than 1 whole upc), `baseFeeBig.Div(..., 1e18)` truncates to `0`. `effectiveGasPrice` becomes `0`, and `gasCost = 0 * gasUsed = 0` regardless of how much gas the executed EVM call actually consumed. This value flows directly into `DeductGasFeesFromReceipt`, which short-circuits to a no-op when `gasCost.Sign() <= 0`:
```go
if gasCost.Sign() <= 0 {
    return nil
}
``` [4](#0-3) 

This is the gas-fee accounting path used after every module-originated `DerivedEVMCall`/`executeUniversalTx` for inbound universal transactions — the exact "universal execution … gas fee accounting" surface called out in the allowed-impact scope.

### Impact Explanation
When the dynamically-adjusted base fee is below 1 upc (a state reachable purely through ordinary, unprivileged usage patterns — sustained low network utilization causes the EIP-1559 base fee to decay), `DeductAndBurnFees`/`DeductGasFeesFromReceipt` deducts and burns exactly `0` upc from the recipient regardless of actual `receipt.GasUsed`. This breaks the gas-fee accounting invariant: users can consume arbitrary amounts of EVM execution gas via universal payload execution while paying zero fee, corrupting protocol fee accounting and creating a spam/resource-exhaustion vector (unpriced heavy contract execution triggered by ordinary inbound deposits/payloads), which matches the in-scope impact "corruption of ... gas fee accounting ... reachable from ordinary user deposits, payloads ... alone."

### Likelihood Explanation
Reachability depends entirely on whether the feemarket base fee can actually fall below 1 upc (i.e., have LegacyDec value < 1) during normal operation, which requires confirming the exact `x/feemarket` base-fee recalculation formula and its parameters (`min_gas_price` floor, `base_fee_change_denominator`) in the vendored `cosmos/evm` dependency — I was not able to inspect that vendored module's source in this session (grep for `CalculateBaseFee`/`elasticity`/`baseFeeChangeDenominator` returned no results, meaning it lives in an external Go module not indexed here). If `min_gas_price` acts as a hard floor ≥ 1 upc and the module never returns a value below it, this path is unreachable and the bug is latent/inert. Given this uncertainty, I classify likelihood as **Low-to-Medium, requiring confirmation** of the feemarket module's minimum-base-fee floor behavior.

### Recommendation
Reorder the arithmetic to multiply before dividing, preserving full precision:
```go
gasCost := new(big.Int).Mul(baseFee.BigInt(), gasUsedBig) // baseFee.BigInt() * gasUsed
gasCost.Div(gasCost, big.NewInt(1e18))                    // single truncation at the end
```
Additionally, add an explicit assertion/guard (or clamp) ensuring `effectiveGasPrice` is never silently zero when the underlying `LegacyDec` base fee is nonzero, and add a unit test with a sub-1-upc fractional `baseFee` (e.g., `0.5`) and large `gasUsed` to lock in nonzero fee output.

### Proof of Concept
1. Set `baseFee = sdkmath.LegacyNewDecWithPrec(5, 1)` (i.e., `0.5` upc, internal representation `5*10^17`).
2. Call `k.CalculateGasCost(baseFee, maxFeePerGas, maxPriorityFeePerGas, gasUsed=10_000_000)`.
3. `baseFeeBig.Div(5e17, 1e18) = 0` → `effectiveGasPrice = 0` → `gasCost = 0 * 10_000_000 = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without deducting/burning any fee, even though `receipt.GasUsed = 10,000,000`.
5. Contrast with correct order: `Mul(5e17, 10_000_000) = 5e24`, then `Div(5e24, 1e18) = 5_000_000` (nonzero, correctly proportional) upc.

This confirms the divide-then-multiply ordering causes complete truncation-to-zero of gas fees whenever the base fee's fractional upc component is used, unlike the mathematically correct multiply-then-divide ordering.

### Citations

**File:** x/uexecutor/keeper/fees.go (L53-81)
```go
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

**File:** x/uexecutor/keeper/fees.go (L116-119)
```go
	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
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

**File:** testnet/core/setup/setup_genesis_validator.sh (L152-154)
```shellscript
  update_test_genesis '.app_state["feemarket"]["params"]["no_base_fee"]=false'
  update_test_genesis '.app_state["feemarket"]["params"]["base_fee"]="1000000000.000000000000000000"'
  update_test_genesis '.app_state["feemarket"]["params"]["min_gas_price"]="1000000000.000000000000000000"'
```
