### Title
Silent truncation of fractional base-fee mantissa causes systematic gas-fee under-collection in payload execution - (File: x/uexecutor/keeper/fees.go)

### Summary
`x/uexecutor/keeper/fees.go`'s `CalculateGasCost` mixes a scaled fixed-point type (`sdkmath.LegacyDec`, Cosmos SDK's 18-decimal-precision mantissa representation — the same "Excessive Indirection" pattern as Compound's `Exp`) with an unscaled `*big.Int` "upc" representation, converting between them via a bare integer division on the assumption that the scaled value is "always a whole number of upc." That invariant is not enforced anywhere in the scoped code and is not guaranteed by the feemarket base-fee update mechanism, so any fractional remainder is silently discarded every time gas fees are computed for `ExecutePayloadV2`.

### Finding Description
`CalculateGasCost` unwraps the feemarket's `baseFee` (`sdkmath.LegacyDec`) back into a `upc`-denominated `*big.Int` like this: [1](#0-0) 

The comment explicitly documents the (unverified) assumption: [2](#0-1) 

`baseFee.BigInt()` returns the raw internal mantissa of the `LegacyDec` (value × 10¹⁸ — this is Cosmos SDK's generic arithmetic precision for *all* `Dec` values, unrelated to whether the domain quantity it represents is fractional). The code then does `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))`, an integer division that truncates toward zero. If the feemarket's EIP-1559 base-fee adjustment (`baseFee ± gasUsedDelta/gasTarget/BaseFeeChangeDenominator`, computed with `LegacyDec` division) ever produces a value that is not an exact multiple of `1e18` at the mantissa level, the fractional remainder is dropped with no error, no logging beyond a debug line, and no compensating carry-forward.

`effectiveGasPrice` derived from this truncated value is then used directly to compute `gasCost = effectiveGasPrice * gasUsed`, which is burned from the caller in `DeductGasFeesFromReceipt` → `DeductAndBurnFees`: [3](#0-2) 

This is reached on every ordinary user payload execution through `ExecutePayloadV2`: [4](#0-3) 

Since Go's integer division truncates toward zero (i.e., always rounds *down* for positive values), the derived `effectiveGasPrice`/`gasCost` can never be rounded up — only down or exact. Every UEA/CEA payload execution that goes through this path is charged a gas price that is less than or equal to the feemarket's true, more-precise base fee, with no accompanying validation that the discarded remainder was actually zero.

### Impact Explanation
This falls in the "corruption of ... gas fee accounting" bucket of the allowed-impact gate. It is reachable by any unprivileged user submitting `MsgExecutePayload` — no validator, TSS, or admin privilege required — and honest validators/nodes would all compute the same (silently wrong) truncated value deterministically, so it would not cause consensus divergence, but it does cause the protocol to systematically under-burn the fee it believes it is charging relative to the feemarket's actual computed base fee, understating real gas cost accounting across the fleet of `ExecutePayloadV2` calls.

### Likelihood Explanation
Likelihood depends entirely on whether the upstream `x/feemarket` (cosmos/evm fork) base-fee adjustment can produce a `LegacyDec` value whose internal mantissa is not an exact multiple of `1e18`. That module's EIP-1559-style adjustment formula (`baseFee.Mul(...).Quo(...)`) is arithmetic over `LegacyDec`, which is capable of producing values at full 18-decimal precision; nothing in the scoped `x/uexecutor` code (nor evidently in the feemarket wrapper interface used here, which only exposes `GetBaseFee(ctx) math.LegacyDec`) asserts or enforces that the result is always an exact "whole upc" value before it reaches `CalculateGasCost`. I could not fully verify the upstream feemarket module's internals from this repository (it's an external dependency, `cosmos/evm`), so likelihood is assessed as **plausible but not confirmed** — the risk is real given the unchecked assumption embedded directly in the comment, but confirming actual exploitability requires inspecting the vendored `x/feemarket.CalculateBaseFee` implementation.

### Recommendation
- Do not silently assume the mantissa is a whole multiple of `1e18`. Assert it (or use `TruncateInt()`/`Ceil().TruncateInt()` deliberately) and decide explicitly whether truncation should round up (protocol-favoring) or down, rather than depending on undocumented Div semantics.
- Enforce the `Mantissa` naming/suffix convention referenced in the report: `baseFee` should be renamed to make explicit it's a scaled `Dec`, and the unscaling step should live in one shared, tested helper rather than being duplicated with an inline comment-only invariant.
- Add a defensive check that logs/errors (or accounts for the remainder) if `baseFeeBig` is not evenly divisible by `1e18`, so silent precision loss cannot occur without an audit trail.

### Proof of Concept
Conceptual PoC (not concretely exploitable without upstream feemarket source confirmation):
1. Drive the feemarket base fee through several EIP-1559 adjustment blocks (varying gas usage vs. target) so its `LegacyDec` internal mantissa is not an exact multiple of `1e18`, e.g., mantissa `= N*1e18 + r` where `0 < r < 1e18`.
2. Submit `MsgExecutePayload`, which flows through `ExecutePayloadV2` → `DeductGasFeesFromReceipt` → `CalculateGasCost`. [5](#0-4) 
3. `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates to `N`, discarding `r`, so `effectiveGasPrice = N` instead of the true fractional-upc-adjusted rate.
4. Every payload execution is billed `gasCost = N * gasUsed` instead of the feemarket's intended (fractionally higher) rate, understating burned fees across all executions relying on this path.

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

**File:** x/uexecutor/keeper/fees.go (L116-140)
```go
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

**File:** x/uexecutor/keeper/execute_payload.go (L39-53)
```go
	cacheCtx, writeCache := sdkCtx.CacheContext()
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
