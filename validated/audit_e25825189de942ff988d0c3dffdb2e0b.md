### Title
Double division by 1e18 in `CalculateGasCost` zeroes out universal-payload gas fees, letting users execute UEA payloads for free - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` divides `baseFee.BigInt()` by `1e18` a second time, even though `cosmossdk.io/math.LegacyDec.BigInt()` already returns the value with the 18-decimal fixed-point precision stripped out (i.e. the whole-number `upc` amount). For any realistic base fee (well below `1e18` upc), this extra division truncates the effective gas price to `0`, so `gasCost = effectiveGasPrice * gasUsed = 0`. This is the same rounding-to-zero failure mode as the ACO `_getTokenStrikePriceRelation` bug: real value (EVM computation the recipient consumed) is delivered while the "collateral"/fee the recipient owes rounds down to nothing.

### Finding Description
`CalculateGasCost` ( [1](#0-0) ) does:
```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```
`sdkmath.LegacyDec.BigInt()` already performs `chopPrecisionAndRound`, returning the *already-unwrapped* integer value of the decimal (e.g. a `BaseFee` Dec of `1000000000` upc returns `BigInt() == 1000000000`, not `1000000000 * 1e18`). The code's comment incorrectly assumes `BigInt()` returns the raw fixed-point internal representation, and divides by `1e18` again. For any base fee smaller than `1e18` upc — which covers essentially all realistic fee-market values, including the ones used in the repo's own test setups (`math.LegacyNewDec(1000000000)` in `test/integration/uexecutor/execute_payload_test.go:44`) — `baseFeeBig` collapses to `0`.

`effectiveGasPrice` is then set from this zeroed `baseFeeBig` ( [2](#0-1) ), so `gasCost = 0 * gasUsed = 0` regardless of how much EVM gas the universal payload execution actually consumed.

`DeductGasFeesFromReceipt` ( [3](#0-2) ) then hits:
```go
if gasCost.Sign() <= 0 {
    return nil
}
```
and returns without deducting or burning any `upc` from the recipient's account — silently skipping fee collection entirely.

This routine is invoked from the universal execution path — `ExecutePayloadV2` ( [4](#0-3) ), plus `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`, and `msg_execute_payload.go` (all call `DeductGasFeesFromReceipt`). In all these flows real EVM execution (`CallUEAExecutePayload`/`DerivedEVMCall`) happens first, consuming validator computation and state, and the fee-deduction step that is supposed to charge the user for that execution is a no-op.

### Impact Explanation
Every unprivileged user submitting a universal payload for UEA/CEA execution can consume real EVM computation on Push Chain (module-originated `DerivedEVMCall`) without ever paying the intended gas fee in `upc`, because the fee amount always computes to `0` for any realistic `BaseFee`. This breaks the fee/gas-accounting invariant explicitly called out in scope ("corruption of ... gas fee accounting, refund accounting ... UniversalTx state"): the protocol is supposed to burn `gasUsed * effectiveGasPrice` from the executing account but instead burns nothing, effectively letting execution be obtained "for free" — directly analogous to the ACO report where an exerciser was able to redeem value while the collateral/payment leg rounded to zero. This does not directly drain existing user balances, but it is a systemic value/accounting corruption: the protocol's expected gas revenue collection is unconditionally bypassed, enabling free/unmetered universal execution and effectively a DoS-by-free-computation vector (attackers can spam expensive UEA payload executions at zero marginal cost).

### Likelihood Explanation
High — the bug is unconditional and triggers on the default/expected code path for essentially any `BaseFee` value below `1e18` upc, which includes the values used in the codebase's own integration tests (`1e9`). No adversarial input crafting is required beyond normal usage of `ExecutePayloadV2`/`MsgExecutePayload`; every gas-charging call to `CalculateGasCost` is affected as long as `BaseFee < 1e18`.

### Recommendation
Remove the redundant division by `1e18` in `CalculateGasCost` — `baseFee.BigInt()` already returns the whole-number `upc` value; use it directly as `effectiveGasPrice` (or use `baseFee.TruncateInt().BigInt()` for clarity) without the extra `Div(..., 1e18)`. Add unit/integration tests asserting that for a realistic non-trivial `BaseFee` (e.g. `1e9` or `1e12` upc) and non-zero `gasUsed`, `DeductGasFeesFromReceipt` actually deducts and burns a non-zero `upc` amount from the recipient, and that the burned amount matches `gasUsed * baseFee` exactly.

### Proof of Concept
1. Set `FeeMarketKeeper` params so `BaseFee = math.LegacyNewDec(1_000_000_000)` (1e9 upc), as done in `test/integration/uexecutor/execute_payload_test.go:44`.
2. Submit any `MsgExecutePayload`/universal payload whose UEA execution consumes non-zero EVM gas (`receipt.GasUsed > 0`).
3. Trace `DeductGasFeesFromReceipt` → `CalculateGasCost`: `baseFeeBig := baseFee.BigInt()` yields `1_000_000_000`; `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates this to `0`; `gasCost = 0 * gasUsed = 0`.
4. `DeductGasFeesFromReceipt` hits `gasCost.Sign() <= 0` and returns `nil` without calling `DeductAndBurnFees` — no `upc` is transferred from or burned for the recipient account, despite real EVM gas having been consumed.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-60)
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

**File:** x/uexecutor/keeper/execute_payload.go (L40-53)
```go
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```
