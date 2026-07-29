## Title
Premature truncation of `baseFee` before multiplication by `gasUsed` causes systematic gas-fee undercharging (up to zero-fee execution) in universal payload gas accounting — (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` converts the `sdkmath.LegacyDec` base fee to a raw integer by dividing its internal 1e18-scaled representation *before* multiplying by `gasUsed`, instead of multiplying first and dividing last. This is the exact division-before-multiplication anti-pattern from the referenced `NukeFund.calculateAge()` finding: any fractional component of the base fee below 1 upc is discarded up front, and if the base fee itself is below 1 upc, the entire gas charge collapses to zero regardless of how much gas was actually consumed.

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// ...
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```
followed later by [2](#0-1) 
```go
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasUsedBig := new(big.Int).SetUint64(gasUsed)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
```

The comment asserts "the base fee is always a whole number of upc -- no fractional upc exists," but that invariant is not enforced anywhere in this code path. The base fee is stored and mutated as an `sdkmath.LegacyDec` by the standard cosmos-evm `feemarket` module, whose EIP-1559-style block-by-block base-fee adjustment (percentage-based increase/decrease relative to gas target) naturally produces fractional decimal values over time — it is not guaranteed to always land on an integer upc boundary. Genesis values are seeded as whole numbers (e.g. `"1000000000.000000000000000000"`), but nothing prevents post-genesis adjustment from drifting into a fractional value.

Because `baseFeeBig.Div(..., 1e18)` (integer division, truncating toward zero) happens *before* the multiplication by `gasUsed`, the mathematically correct amount `floor(baseFee_raw * gasUsed / 1e18)` is replaced by `floor(baseFee_raw / 1e18) * gasUsed`. If `baseFee < 1` upc (i.e., `baseFee_raw < 1e18`), `effectiveGasPrice` truncates to `0`, and `gasCost` becomes `0` no matter how large `gasUsed` is — an unprivileged, ordinary-user-reachable path (submitting any `UniversalPayload`/CEA execution whose gas is deducted via `DeductGasFeesFromReceipt`) that produces zero fee burn for real EVM gas consumption. [3](#0-2) 
`DeductGasFeesFromReceipt` calls `CalculateGasCost` and then `DeductAndBurnFees`, which transfers and burns the computed (potentially incorrectly zero or undercharged) amount from the recipient's account — directly corrupting protocol gas-fee accounting.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" in scope. Any unprivileged user submitting a gas-and-payload inbound whose `receipt.GasUsed` is non-zero, when the on-chain base fee happens to be below 1 upc (a legitimate, reachable state as the fee market adjusts down during low congestion, or simply through fractional drift), will have `DeductAndBurnFees` burn less than the honestly-computed amount — in the worst case zero — for actual EVM execution that was performed. This is a protocol-level fund-accounting error (undercharging users for consumed gas), not merely a display/precision nuisance, since the deducted/burned amount is the authoritative on-chain state change.

### Likelihood Explanation
Likelihood depends on the base fee actually falling below 1 upc or acquiring fractional drift, which is a function of the feemarket module's adjustment mechanics and configured minimum/base fee floor rather than of the attacker directly. No privileged action is required to trigger the bug once such a state occurs — any ordinary payload execution exercises the vulnerable code path.

### Recommendation
Do not truncate the base fee to a whole upc value before multiplying by `gasUsed`. Multiply the raw scaled `baseFee` (or `baseFee.MulInt64(gasUsed)`) first, then divide by `1e18` (i.e., use `sdkmath.LegacyDec` arithmetic or `big.Int` multiply-then-divide, matching the recommended fix pattern: numerator variables multiplied first, then divided by the fixed denominator), for example:
```go
gasCostDec := baseFee.MulInt64(int64(gasUsed)) // still full precision
gasCost := gasCostDec.TruncateInt().BigInt()   // or Ceil() depending on desired rounding direction
```
This preserves EIP-1559 accounting precision instead of truncating the price component independently of the volume component.

### Proof of Concept
Given `baseFee = sdkmath.LegacyDec` representing `0.9` upc (raw internal value `9e17`, i.e., `900000000000000000`), and `gasUsed = 21000`:
- Correct amount: `0.9 * 21000 = 18900` upc (as an integer, `floor(9e17 * 21000 / 1e18) = 18900`).
- Actual code: `baseFeeBig.Div(9e17, 1e18) = 0`, then `effectiveGasPrice(0) * 21000 = 0`.

Result: `DeductAndBurnFees` burns `0` upc for a transaction that consumed `21000` real EVM gas — a 100% discrepancy versus the mathematically correct fee, reachable by any user submitting a payload execution while the network base fee sits below 1 upc.

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

**File:** x/uexecutor/keeper/fees.go (L74-81)
```go
	effectiveGasPrice := new(big.Int).Set(baseFeeBig)
	// if tipPlusBase.Cmp(maxFeePerGas) == -1 {
	// 	effectiveGasPrice = tipPlusBase
	// }

	// Step 4: Calculate final gas cost: effectiveGasPrice * gasUsed
	gasUsedBig := new(big.Int).SetUint64(gasUsed)
	gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
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
