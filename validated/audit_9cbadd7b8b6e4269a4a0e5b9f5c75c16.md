## Title
Premature truncation of `baseFee` in `CalculateGasCost` causes gas-fee undercharging, amplified by `gasUsed` — ([File: x/uexecutor/keeper/fees.go])

### Summary
The external esMETA bug root cause is: computing a rate via **division first**, then multiplying that already-truncated value by a large factor (elapsed time), so the truncation error gets amplified and the user permanently loses funds relative to the correct accounting. `x/uexecutor/keeper/fees.go`'s `CalculateGasCost` contains the same rounding-order defect applied to gas-fee accounting: it truncates `baseFee` to whole `upc` **before** multiplying by `gasUsed`, instead of multiplying first and rounding the final result.

### Finding Description
`CalculateGasCost` receives `baseFee` as an `sdkmath.LegacyDec` (18-decimal fixed point) from the feemarket module: [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... the base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

`baseFee.BigInt()` returns the raw internal representation (`value * 1e18`). Dividing this by `1e18` truncates any sub-`upc` fractional component of the base fee **before** it is used anywhere else. The code then immediately multiplies this already-truncated integer by `gasUsed`: [2](#0-1) 

The inline comment asserts "the base fee is always a whole number of upc," but nothing in this module enforces that invariant — the base fee is set and adjusted by the feemarket module's EIP-1559 elasticity mechanism (`GetBaseFee`), which is a standard multiplicative/divisive adjustment over `LegacyDec` and routinely produces fractional `upc` values as it converges toward the target gas usage. `x/uexecutor/keeper/expected_keepers.go` shows the dependency is a plain `LegacyDec` getter with no rounding guarantee: [3](#0-2) 

This is exactly the esMETA pattern: `rate = amount / time` (division first) then `claimable = rate * elapsedTime` (multiply by a large factor), instead of `claimable = amount * elapsedTime / time`. Here it's `effectiveGasPrice = truncate(baseFee)` then `gasCost = effectiveGasPrice * gasUsed`, instead of `gasCost = truncate(baseFee_raw * gasUsed / 1e18)`. Any fractional remainder in `baseFee` (up to just under 1 whole `upc`) is dropped *before* being multiplied by `gasUsed`, so the truncation error is amplified by the gas-used factor rather than confined to a sub-unit rounding of the final total.

`CalculateGasCost` feeds `DeductGasFeesFromReceipt`, which burns exactly this (undercharged) amount from the payload-executing recipient's smart account: [4](#0-3) 

This is invoked from the ordinary, unprivileged universal-execution payload path (`execute_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`, `msg_execute_payload.go`), i.e. every user-submitted `GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`/`PAYLOAD` inbound or `MsgExecutePayload` triggers this deduction.

### Impact Explanation
Every payload execution burns `truncate(baseFee) * gasUsed` instead of the mathematically correct `baseFee * gasUsed` (rounded once at the end). Because the truncation happens before the multiplication, the chain systematically undercharges gas fees whenever `baseFee` carries a fractional `upc` component — which is the normal case for an EIP-1559-style adjusting base fee, not an edge case. The magnitude of undercharge per transaction scales with `gasUsed` (up to just under 1 `upc` short per gas-price unit, times gas used), and this recurs on every payload-bearing inbound processed by every node. This is a direct corruption of gas-fee accounting (`DeductAndBurnFees` burns less than the protocol should collect), degrading protocol fee revenue accounting over time in a way that is invisible per-transaction but accumulates chain-wide — the same "gradual imbalance" impact called out in the source report.

### Likelihood Explanation
This triggers on the default, unprivileged transaction path (any user submitting a payload-bearing inbound/`MsgExecutePayload`) with no special conditions beyond the base fee having a non-zero fractional `upc` component, which is the ordinary steady-state behavior of an EIP-1559 elasticity-adjusted base fee. No malicious validator, relayer, or privileged actor is required.

### Recommendation
Do not truncate `baseFee` before multiplying by `gasUsed`. Compute `gasCost` from the full-precision `LegacyDec` (or the raw `1e18`-scaled `big.Int`) multiplied by `gasUsed`, and only truncate/round once at the very end:
```go
gasCostRaw := new(big.Int).Mul(baseFee.BigInt(), gasUsedBig) // still 1e18-scaled
gasCost := new(big.Int).Div(gasCostRaw, big.NewInt(1e18))    // single rounding at the end
```
This mirrors the esMETA fix pattern: extend precision through the multiplication and only divide back down once, at the final step.

### Proof of Concept
1. Set feemarket base fee to a value with a fractional `upc` remainder, e.g. `baseFee = 1_000_000_000.999999999999999999` upc (representable as a `LegacyDec`), which is a normal outcome of the EIP-1559 elasticity adjustment over blocks.
2. Submit a payload-bearing inbound (e.g. `GAS_AND_PAYLOAD`) with `gasUsed = 500,000`.
3. `CalculateGasCost` truncates `baseFee` down to `1_000_000_000` upc before multiplying, yielding `gasCost = 1_000_000_000 * 500,000 = 500,000,000,000,000` upc.
4. The mathematically correct EIP-1559 fee is `1_000_000_000.999999999999999999 * 500,000 ≈ 500,000,000,499,999.9999...` upc — roughly `500,000` upc less is burned than should be, every single such transaction, systematically favoring the fee-payer over protocol revenue accounting.

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

**File:** x/uexecutor/keeper/fees.go (L73-88)
```go
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

**File:** x/uexecutor/types/expected_keepers.go (L58-61)
```go
// FeeMarketKeeper defines the expected interface for the fee market module.
type FeeMarketKeeper interface {
	GetBaseFee(ctx sdk.Context) math.LegacyDec
}
```
