Based on my investigation, I found a genuine analog to the FEYTraderJoeProduct math-inconsistency bug within Push Chain's own scoped code: the gas-fee computation in `x/uexecutor/keeper/fees.go` performs the DECIMAL_FACTOR-style unscaling (`÷1e18`) **before** the multiplication instead of after, unlike the correct order used elsewhere in the same codebase (e.g. the `minPCOut = quote*95/100` slippage calculations always multiply-then-divide). This ordering silently truncates any fractional-upc component of `baseFee` before it is ever multiplied by `gasUsed`, which can systematically under-collect gas fees.

### Title
Premature division before multiplication in `CalculateGasCost` truncates fractional base fee, under-collecting protocol gas fees - (File: x/uexecutor/keeper/fees.go)

### Summary
`Keeper.CalculateGasCost` divides `baseFee` (an 18-decimal `LegacyDec`) by `1e18` **before** multiplying by `gasUsed`, instead of multiplying first and dividing last. This mirrors the reported bug class exactly: the "decimal factor" is applied in the wrong position relative to the other operand, producing a formula that is inconsistent with the correct order used for the same kind of scaled arithmetic elsewhere in the code (e.g. `minPCOut := quote*95; minPCOut.Div(100)` in `outbound.go`/`execute_inbound_gas.go`, which correctly multiplies before dividing).

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// ...comment asserts baseFee is always a whole number of upc...
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
...
gasUsedBig := new(big.Int).SetUint64(gasUsed)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
```
The code performs `floor(baseFee/1e18) * gasUsed` instead of the mathematically correct `floor(baseFee*gasUsed/1e18)`. The correctness of the shortcut depends entirely on the in-code assumption that `baseFee` is *always* an exact multiple of `1e18` in its `LegacyDec` representation — i.e., that the fee-market base fee can never carry a sub-upc fractional component. Nothing in the scoped `x/uexecutor` code enforces or verifies that invariant; `baseFee` is read directly from `k.feemarketKeeper.GetBaseFee(sdkCtx)` [2](#0-1)  which returns whatever `LegacyDec` value the fee-market's elasticity-based adjustment algorithm computed for that block — a value that is not guaranteed to land on a whole-upc boundary.

If `baseFee` has any fractional-upc remainder, dividing first drops that remainder entirely before it can contribute (via multiplication by `gasUsed`) to the final `gasCost`. For any `gasUsed` greater than 1, this loses more value than a single unit of rounding error would — the loss scales with `gasUsed`.

### Impact Explanation
This is invoked on every gasful `MsgExecutePayload` / inbound-gas-and-payload execution via `DeductGasFeesFromReceipt` [3](#0-2) , which burns the computed `gasCost` from the UEA's balance. Under-computing `gasCost` here means the protocol systematically under-collects (burns less than the EIP-1559 formula intends) gas fees paid by ordinary users for every payload execution whenever the fee-market base fee isn't an exact upc multiple — a corruption of gas-fee accounting reachable purely through default transaction submission, with no privileged actor involved.

### Likelihood Explanation
Triggering requires only that the fee-market's dynamically adjusted base fee, at some block, not be an exact multiple of `1e18` in its internal `LegacyDec` representation — an ordinary, expected outcome of any percentage-based base-fee adjustment algorithm, not an attacker-crafted condition. Every gasful `ExecutePayload`/`ExecuteInboundGasAndPayload` call at such a block is affected.

### Recommendation
Reorder the arithmetic to multiply before dividing, matching the pattern already used elsewhere in the codebase (e.g. the swap slippage calculations): compute `gasCost = baseFee.BigInt() /* full 1e18-scaled value */ * gasUsed`, then divide the *product* by `1e18` once, instead of dividing `baseFee` down to upc units first. This preserves any fractional-upc precision through the multiplication step.

### Proof of Concept
```go
// baseFee = 1.9 upc in LegacyDec form (1_900_000_000_000_000_000), gasUsed = 3
baseFeeBig := big.NewInt(1_900_000_000_000_000_000)
gasUsed := uint64(3)

// Current (buggy) order: divide first
wrong := new(big.Int).Div(baseFeeBig, big.NewInt(1e18))      // = 1
wrong.Mul(wrong, big.NewInt(int64(gasUsed)))                  // = 3 upc

// Correct order: multiply first, divide once
correct := new(big.Int).Mul(baseFeeBig, big.NewInt(int64(gasUsed))) // 5_700_000_000_000_000_000
correct.Div(correct, big.NewInt(1e18))                              // = 5 upc

// wrong (3) != correct (5) -> ~40% fee under-collection for this block
```

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

**File:** x/uexecutor/keeper/fees.go (L97-141)
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
