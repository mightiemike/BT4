### Title
Integer-division truncation in `CalculateGasCost` zeroes out gas-fee accounting when `baseFee` drops below 1 upc - ([File: x/uexecutor/keeper/fees.go])

### Summary
`GlpPricing.usdToGlp` zeroed out for reasonable inputs because a division was applied at the wrong point in the formula, truncating small values to zero before the final scaling. The same class of bug exists in Push Chain's `CalculateGasCost` (`x/uexecutor/keeper/fees.go`), where the fee-market `baseFee` is unwrapped from its `LegacyDec` 18-decimal internal representation via integer division (`baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))`) *before* it is used to compute `gasCost = effectiveGasPrice * gasUsed`. If the true base fee is a fraction of 1 upc, this integer division truncates it to `0`, making `effectiveGasPrice = 0` and therefore `gasCost = 0` regardless of `gasUsed`.

### Finding Description
`CalculateGasCost` in [1](#0-0)  takes the fee-market `baseFee` (an `sdkmath.LegacyDec`, which stores values scaled by `1e18` internally) and unwraps it back to a whole-upc integer with:

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

The inline comment asserts "the base fee is always a whole number of upc — no fractional upc exists," but this is an assumption about the fee-market's dynamic base-fee value, not an enforced invariant in this code path. If `baseFee` (as a Dec) is ever less than `1.0` (i.e., sub-1-upc, such as `0.5`), `baseFeeBig.Div` performs Go's truncating integer division and yields `0`. `effectiveGasPrice` is then set from this truncated value [2](#0-1) , and the subsequent multiplication `gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)` yields `0` no matter how large `gasUsed` is.

`CalculateGasCost` is invoked from `DeductGasFeesFromReceipt` [3](#0-2) , which is the function responsible for burning the fee owed by a UEA/derived-EVM-call recipient after a real EVM receipt with non-zero `GasUsed`. If `gasCost.Sign() <= 0`, the function returns `nil` and skips `DeductAndBurnFees` entirely — the recipient pays nothing for gas that was actually consumed on-chain.

Unlike `GetOutboundTxGasAndFees`, whose ABI schema was hardened post-audit to read `gasLimitUsed` directly from the contract rather than re-deriving it via division (see the explicit regression guard in `x/uexecutor/keeper/gas_fee_test.go` lines 64-70, referencing "post-audit" fixes), this particular division in `CalculateGasCost` has no equivalent guard, floor check, or minimum-value assertion.

### Impact Explanation
This is a gas/fee-accounting corruption bug: an unprivileged user submitting UEA-derived payload executions (`MsgExecutePayload` → EVM execution → `DeductGasFeesFromReceipt`) would be charged `0` upc for real EVM gas consumed whenever the fee-market's `baseFee` decays below 1 upc. This breaks the protocol's own fee/gas accounting invariant and lets ordinary users consume real, module-subsidized EVM execution for free — a form of unauthorized value extraction/free-riding on protocol-controlled EVM resources, falling under "corruption of ... gas fee accounting" in the allowed-impact list.

### Likelihood Explanation
Exploitability depends entirely on whether the chain's fee-market `baseFee` can legitimately fall below `1.0` (i.e., below `1e18` in the Dec's raw representation) under normal EIP-1559-style adjustment. I was unable to fully confirm this within the available scope: I found no `MinBaseFee` parameter in the searched Go code, and observed configured genesis values of `base_fee = "1000000000.000000000000000000"` (1e9 upc, far above the truncation threshold) in test/dev genesis files (`scripts/test_node.sh`, `testnet/core/setup/setup_genesis_validator.sh`). Whether the underlying `cosmos/evm` feemarket module (an external fork dependency not fully indexed here) enforces a floor above 1 upc, or can decay the base fee down toward zero over sustained low-usage periods, could not be conclusively verified from the indexed code. If such a floor exists and always keeps `baseFee >= 1 upc`, this specific finding would not be reachable in practice.

### Recommendation
- Do not truncate `baseFeeBig` before validation/multiplication. Perform the `maxFeePerGas` comparison and the final `gasCost` computation using the full-precision `LegacyDec` (or an equivalent high-precision integer scaled consistently), only rounding/truncating to whole-upc units at the very last step (analogous to the GLP fix: move the scaling division to the end of the formula, not the beginning).
- Add an explicit invariant check: if the truncated `baseFeeBig` is `0` while the underlying Dec is non-zero, treat this as an error (or round up) rather than silently proceeding with a zero effective gas price.
- Add a unit test analogous to `TestUniversalCoreABI_GetOutboundTxGasAndFees_Has6Outputs`'s regression-guard style, asserting that `CalculateGasCost` never returns `0` for a non-zero `baseFee` and non-zero `gasUsed`.

### Proof of Concept
Conceptual reproduction (based on reading `CalculateGasCost`'s logic; not independently executed against a running node):
1. Assume the fee-market base fee decays (via normal EIP-1559 decrease-on-low-usage behavior) to `0.5` upc, represented internally as `LegacyDec` raw value `5 * 10^17`.
2. A user submits a `MsgExecutePayload` whose EVM execution consumes real gas, e.g. `gasUsed = 1_000_000`, with `maxFeePerGas >= 1` (validation at line 63 passes trivially against the truncated `baseFeeBig`, which is `5*10^17 / 10^18 = 0`).
3. `effectiveGasPrice := new(big.Int).Set(baseFeeBig)` is `0`.
4. `gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)` evaluates to `0`.
5. In `DeductGasFeesFromReceipt`, `gasCost.Sign() <= 0` is true, so the function returns `nil` without calling `DeductAndBurnFees` — the user's real EVM gas consumption goes completely unbilled.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-61)
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

```

**File:** x/uexecutor/keeper/fees.go (L72-81)
```go
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

**File:** x/uexecutor/keeper/fees.go (L97-127)
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
```
