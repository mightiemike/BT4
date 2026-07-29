## Finding

### Title
Gas fee accounting silently bypassed when computed base fee truncates to zero — free EVM execution for UEA payloads - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`DeductGasFeesFromReceipt` / `CalculateGasCost` compute the fee a user's UEA must pay for the EVM gas it consumed via `MsgExecutePayload` / `ExecutePayloadV2`. The computation first divides the `LegacyDec` base fee by `1e18` to convert it into whole-`upc` units, and then treats any non-positive result as "nothing to charge" and returns success without deducting or erroring. This mirrors the anchor-price-zero class of bug: a boundary value (price/fee == 0) silently disables the enforcement path that is supposed to gate the operation, instead of the system defining one consistent behavior for the zero case.

### Finding Description
`CalculateGasCost` in [1](#0-0)  converts the `LegacyDec` base fee to a whole-`upc` integer:

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

`baseFee.BigInt()` returns the internal 18-decimal-scaled representation of the `LegacyDec`. Any base fee value strictly less than `1` `upc` (e.g. `0.5`, `0.99...`) truncates to `0` after this integer division. The code's own comment asserts "the base fee is always a whole number of upc," but that is an assumption about `x/feemarket`'s dynamic base-fee adjustment, not an invariant enforced anywhere in this codebase — `x/feemarket`'s base fee moves up/down by a `LegacyDec` fraction every block based on gas utilization and can organically settle below `1e18` (1 upc) during sustained low usage.

Once `baseFeeBig` is `0`:
- The pre-check `if maxFeePerGas.Cmp(baseFeeBig) < 0` in [2](#0-1)  is trivially satisfied for any non-negative `maxFeePerGas` a caller supplies.
- `effectiveGasPrice := new(big.Int).Set(baseFeeBig)` is `0`, so `gasCost := effectiveGasPrice * gasUsed` is `0` regardless of how much EVM gas the payload actually consumed.

Back in `DeductGasFeesFromReceipt`, [3](#0-2) :
```go
gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
...
if gasCost.Sign() <= 0 {
    return nil
}
```
A zero `gasCost` is treated as "no error, nothing to bill" — identical in effect to a legitimately fee-free call — rather than being flagged or bounded to a minimum charge. This function is invoked from every user-reachable payload-execution path: `ExecutePayload` (direct `MsgExecutePayload`) at [4](#0-3)  and `ExecutePayloadV2` (inbound-triggered UEA execution) at [5](#0-4) , as well as the CEA smart-contract execution paths in `execute_inbound_funds_and_payload.go` / `execute_inbound_gas_and_payload.go`.

This is the same bug shape as the reported issue: the enforcement of "pay for consumed gas" degenerates into "no enforcement at all" exactly at the boundary value zero, and this zero condition is reachable purely through ordinary network conditions (feemarket base fee decay) — no privileged actor or malicious validator is required. The result is unbounded, unmetered EVM execution funded entirely by the protocol/validators (real EVM gas is spent to execute the call, but the user pays `0` `upc` for it), corrupting gas-fee accounting, one of the explicitly in-scope invariants ("gas fee accounting ... corruption").

### Impact Explanation
When the on-chain base fee (a `LegacyDec`) is below `1` `upc`, every `MsgExecutePayload` / inbound-triggered payload execution silently skips fee deduction, letting any unprivileged user execute arbitrary EVM calls through their UEA (subject only to the `GasLimit` bound) without paying gas — a persistent, protocol-wide fee bypass rather than an isolated edge case. Because the check silently returns `nil` instead of using a floor/ceil rounding or minimum charge, this is a real corruption of the gas-fee accounting invariant called out in the Push Chain impact gate, and can be sustained for as long as the feemarket's base fee stays below 1 `upc` (which can persist for many blocks depending on demand).

### Likelihood Explanation
Triggering this does not require an attacker to craft anything malicious — it requires only that the network's dynamically-adjusting base fee (governed by `x/feemarket`, following standard EIP-1559-style block-utilization decay) drops below `1e18` in its `LegacyDec` representation, which is expected to happen naturally during periods of low chain usage (e.g., low traffic testnets/early mainnet, or any period after several low-utilization blocks). Any ordinary user submitting a normal `MsgExecutePayload` during such a window benefits from (or can deliberately wait for/trigger via idle blocks) fee-free execution.

### Recommendation
Do not silently skip fee deduction when the computed `gasCost` is zero due to truncation. Either:
- Preserve fractional precision through the whole computation (keep `baseFee` as `LegacyDec` and only round up when converting the final charge to an integer `upc` amount, e.g. `Ceil()` instead of truncating early via `Div`), or
- Enforce a minimum non-zero gas cost per unit of gas used whenever `receipt.GasUsed > 0`, so an attacker/organic near-zero base fee cannot produce `gasCost == 0`.
Additionally, add explicit unit tests asserting that a sub-`1e18` base fee combined with non-zero `GasUsed` still results in a positive fee charge (or an explicit, intentional "gasless" state guarded by a dedicated flag rather than an incidental zero from integer division).

### Proof of Concept
1. Let `x/feemarket` base fee decay (via successive low-gas-utilization blocks) to a `LegacyDec` value below `1e18` internal representation (e.g., `0.9 upc`).
2. Submit `MsgExecutePayload` with any `MaxFeePerGas` ≥ `0` and a payload that consumes non-trivial EVM gas (e.g., 21000+ gas) through a funded UEA.
3. Observe: `CalculateGasCost` computes `baseFeeBig = 0` after `Div(..., 1e18)`; `gasCost = 0 * gasUsed = 0`.
4. `DeductGasFeesFromReceipt` returns `nil` without calling `DeductAndBurnFees`; the UEA/user's `upc` balance is unchanged despite genuine EVM gas having been consumed and paid for by the module/validators.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-65)
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-97)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-56)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
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

	// Both succeeded — commit EVM state and fee deduction together.
	writeCache()
```
