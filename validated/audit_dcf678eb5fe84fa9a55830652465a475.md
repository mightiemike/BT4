### Title
Truncating base-fee unwrap in `CalculateGasCost` can zero out module-collected gas fees for universal payload execution - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` unwraps the feemarket's `LegacyDec` base fee into a `*big.Int` upc amount via integer division by `1e18`, on the documented assumption that "the base fee is always a whole number of upc." [1](#0-0)  That assumption is not enforced anywhere in the scoped code I could find; the feemarket module's dynamic EIP-1559-style base fee (fetched via `k.feemarketKeeper.GetBaseFee(sdkCtx)`) [2](#0-1)  can in principle drift to a fractional value below `1` upc as it decays under sustained low network usage, since `LegacyDec` supports arbitrary sub-unit precision. If that happens, `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates to `0`, `effectiveGasPrice` becomes `0`, and `gasCost := effectiveGasPrice * gasUsed` is `0` regardless of `gasUsed`.

### Finding Description
`DeductGasFeesFromReceipt` calls `CalculateGasCost` to compute the upc cost charged to a UEA/CEA recipient for gas consumed by a module-originated `DerivedEVMCall` (universal payload execution). [3](#0-2)  When the computed `gasCost.Sign() <= 0`, the function returns `nil` immediately, skipping `DeductAndBurnFees` entirely: [4](#0-3) . Because `effectiveGasPrice` is derived purely from the truncated `baseFeeBig` (the `maxPriorityFeePerGas` path is explicitly disabled/commented out) [5](#0-4) , any base fee below 1 upc collapses the entire fee-accounting path to zero, and every subsequent universal payload execution is processed for free — no burn, no debit from the recipient's balance — until the base fee climbs back above 1 upc.

This is structurally the same bug class as the reported `UniswapOracle.getTokenPrice` issue: a scaling/precision constant is divided into an intermediate value under an unstated "this value is always a whole number" assumption, and when that assumption breaks, integer division silently zeroes the result instead of correctly handling sub-unit precision.

### Impact Explanation
If triggered, this corrupts the gas-fee accounting invariant for universal-execution gas billing: users get UEA/CEA payload execution without paying the fee the protocol is supposed to burn, i.e., a systemic under-collection/loss of protocol-controlled fee revenue reachable purely through ordinary market conditions (sustained low chain usage causing the feemarket base fee to decay below 1 upc), with no privileged actor or malicious validator required. This falls under "corruption of ... gas fee accounting" in the allowed-impact list.

### Likelihood Explanation
Uncertain/likely low in practice. I could not locate the feemarket module's base-fee decay parameters (`base_fee_change_denominator`, `elasticity_multiplier`, minimum floor) in this repository's scoped code — the feemarket implementation appears to be an external/vendored Cosmos EVM dependency, and genesis configs I found set `base_fee` around `1e6`–`1e9` upc [6](#0-5) . Whether the feemarket module's base fee can actually decay all the way below `1` (as opposed to being floored at some minimum, which many EIP-1559 fee-market implementations enforce) is not verifiable from the code visible to me. If the vendored feemarket module enforces a minimum base fee ≥ some non-trivial floor, this bug is unreachable in practice. I flag this as an analog worth checking rather than a confirmed, fully-triggerable vulnerability, since the root-cause assumption ("base fee is always a whole upc") is asserted only in a comment and not verified against the actual feemarket parameter bounds.

### Recommendation
- Verify the feemarket module's base-fee floor/decay bounds; if a fractional base fee below 1 upc is reachable, replace the integer-truncating unwrap with a rounding-aware conversion (e.g., use `baseFee.Ceil().RoundInt()` or `baseFee.RoundInt()` instead of `LegacyDec.BigInt()` followed by raw division), so sub-unit base fees round up to at least 1 upc rather than to 0.
- Alternatively, add an explicit invariant check/assertion that `baseFeeBig` (post-unwrap) is non-zero whenever the pre-unwrap `LegacyDec` value is positive, and fail loudly (return an error) instead of silently proceeding with a zero effective gas price.

### Proof of Concept
Conceptual (not executed, since I only have read access to the index):
1. Assume feemarket allows base fee to decay via its per-block adjustment algorithm during a sustained period of low EVM usage.
2. Once `GetBaseFee` returns a `LegacyDec` value strictly between `0` and `1` upc (e.g., `"0.500000000000000000"`), `CalculateGasCost` computes `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` → `0`.
3. `maxFeePerGas.Cmp(baseFeeBig) < 0` check trivially passes for any non-negative `maxFeePerGas`.
4. `effectiveGasPrice = 0`, so `gasCost = 0 * gasUsed = 0` regardless of how much gas the universal payload execution actually consumed.
5. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil`, skipping `DeductAndBurnFees` — the recipient's UEA/CEA payload executes without any upc being burned.

Confirming step 1 (whether the vendored feemarket module's base fee can actually reach sub-1-upc values) requires inspecting the feemarket module's decay implementation, which is not fully present in this repository's indexed code — a background agent with full repository/dependency access should verify this before treating the finding as confirmed exploitable.

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

**File:** x/uexecutor/keeper/fees.go (L67-77)
```go
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
```

**File:** x/uexecutor/keeper/fees.go (L97-124)
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
```

**File:** x/uexecutor/keeper/fees.go (L125-127)
```go
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
