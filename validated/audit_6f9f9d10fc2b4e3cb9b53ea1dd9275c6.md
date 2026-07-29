### Title
Integer-truncation of `baseFee` to zero silently zeroes out UEA/CEA gas-fee accounting — (File: x/uexecutor/keeper/fees.go)

### Summary
The external report flags OUSD's `changeSupply` for letting a computed rate (`rebasingCreditsPerToken`) become `0` through unchecked division, corrupting downstream accounting invariants. The scoped analog is `CalculateGasCost` in `x/uexecutor/keeper/fees.go`, which converts the feemarket `baseFee` (an 18-decimal `LegacyDec`) into a whole-`upc` `big.Int` via unchecked integer division by `1e18`, then multiplies by `gasUsed` to derive the fee actually burned from the recipient's UEA/CEA account.

### Finding Description
`CalculateGasCost` does:
```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
``` [1](#0-0) 

This assumes the on-chain `baseFee` (from `feemarketKeeper.GetBaseFee`) is always ≥ `1e18` (i.e., ≥ 1 whole `upc`), per the code's own comment. If the feemarket's dynamically-adjusted `baseFee` ever falls below `1e18` (e.g., sustained low block utilization drives EIP‑1559-style base fee down), `baseFeeBig.Div(...)` truncates to `0`, making `effectiveGasPrice == 0` and therefore `gasCost == 0` regardless of `gasUsed`.

The caller `DeductGasFeesFromReceipt` then hits its early-out:
```go
if gasCost.Sign() <= 0 {
    return nil
}
``` [2](#0-1) 
which skips `DeductAndBurnFees` entirely — i.e., no fee is ever deducted/burned from the UEA/CEA recipient account for that universal-payload execution, even though real EVM gas was consumed to execute it (module-originated `DerivedEVMCall`).

### Impact Explanation
This breaks the gas-fee accounting invariant for universal execution: an unprivileged user submitting ordinary inbound transactions that trigger payload execution via `DeductGasFeesFromReceipt` could execute EVM payloads on Push Chain at zero cost whenever `baseFee < 1e18` (sub-1-upc), since no min-payment floor exists in this specific accounting path (this bypass is independent of the transaction-level `MinGasPriceDecorator` ante check, which only governs the outer Cosmos tx fee, not the module-originated internal payload-execution fee deduction). This is a fee/accounting-corruption impact reachable purely by ordinary user deposits/payloads with honest validators, matching the "gas fee accounting" impact category, though it results in lost protocol revenue/free execution rather than fund theft from other users.

### Likelihood Explanation
Likelihood depends entirely on whether the feemarket parameters (`BaseFeeChangeDenominator`, `MinGasPrice`/floor, `ElasticityMultiplier`) as configured in this repo's genesis can ever let `GetBaseFee` drop below `1e18` in practice. I was not able to confirm the concrete feemarket genesis parameters (`MinGasPrice`/base-fee floor) within the available index to determine whether a hard floor at ≥1 `upc` is enforced elsewhere (e.g., in `app/app.go` module wiring or genesis defaults), so I cannot confirm this is currently reachable versus already prevented by a floor. This is the main uncertainty limiting confidence in exploitability.

### Recommendation
- Add an explicit guard in `CalculateGasCost` (or `DeductGasFeesFromReceipt`) that treats a computed `effectiveGasPrice`/`gasCost` of `0` from a non-zero `gasUsed` and non-zero `baseFee` as an error/floor to a minimum of `1`, rather than silently truncating and skipping fee deduction.
- Alternatively, perform the base-fee conversion using `sdkmath.LegacyDec` arithmetic (ceiling rounding) instead of raw `big.Int` division, so sub-`1e18` base fees round up rather than truncate to zero.
- Enforce (and unit-test) a hard floor on `feemarketKeeper`'s `MinGasPrice`/base fee equivalent to at least `1e18` (1 `upc`) at the params level, so this code path's assumption is guaranteed rather than merely commented.

### Proof of Concept
Conceptual (not executed against a live chain, since this requires controlling feemarket base-fee dynamics over multiple blocks, which is outside static analysis):
1. Drive network base fee down via sustained under-target block gas usage until `feemarketKeeper.GetBaseFee(ctx)` returns a `LegacyDec` value less than `1e18` (i.e., less than 1 whole `upc`).
2. Submit an ordinary inbound `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` transaction that results in a module-originated EVM payload execution consuming non-zero `GasUsed`.
3. Observe `CalculateGasCost` returns `gasCost == 0` due to integer truncation, causing `DeductGasFeesFromReceipt` to return `nil` without calling `DeductAndBurnFees`.
4. Confirm via `bankKeeper.GetBalance` on the recipient UEA that no `upc` was deducted/burned despite the payload's EVM execution having consumed gas. [3](#0-2)

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

**File:** x/uexecutor/keeper/fees.go (L97-148)
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

	k.Logger().Debug("gas fees deducted",
		"recipient", recipient.Hex(),
		"gas_used", receipt.GasUsed,
		"gas_cost", gasCost.String(),
	)
	return nil
}
```
