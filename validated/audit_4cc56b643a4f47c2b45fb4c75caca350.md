## Analysis

The external report describes a decimal-truncation bug: `ZapMathLib.computeSharesToTwoCrypto` performs an integer division across two different decimal bases (PT/asset decimals vs. target/vault decimals) without properly normalizing, so a legitimate non-zero value truncates to `0`, breaking a downstream invariant.

The closest reachable analog in this repository is in the Push Chain gas-fee accounting path, specifically `CalculateGasCost` in `x/uexecutor/keeper/fees.go`, used by `DeductGasFeesFromReceipt` to bill recipients for EVM gas consumed by `CallExecuteUniversalTx` (the module-originated `DerivedEVMCall` path for inbound CEA/UEA payload execution).

### Title
Gas-fee truncation-to-zero via unwrap of `LegacyDec` base fee in `CalculateGasCost` - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` unwraps the feemarket's `LegacyDec`-encoded base fee (18-decimal fixed point) into a raw `upc` integer via a single integer division by `1e18`, based on the hardcoded assumption "base fee is always a whole number of upc." If the on-chain base fee ever drops below `1e18` (i.e., below one whole `upc`) in its `LegacyDec` representation, this division truncates to `0`, making `effectiveGasPrice = 0` and therefore `gasCost = 0` regardless of `gasUsed`, causing `DeductGasFeesFromReceipt` to skip fee collection entirely (`gasCost.Sign() <= 0` returns `nil`, i.e. success) while the corresponding EVM execution (and its state changes) is committed.

### Finding Description [1](#0-0) 
`baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` performs integer division and always rounds toward zero — any fractional remainder below `1e18` is silently discarded. The code comment asserts this is always safe because "the base fee is always a whole number of upc," but that invariant is not enforced at this call site; it is an external assumption about the feemarket module's dynamically-adjusted `BaseFee` parameter, which is a `LegacyDec` value that decays multiplicatively based on block gas utilization and is not constrained to be a multiple of `1e18`.

If `baseFeeBig` truncates to `0`: [2](#0-1) 
- The `maxFeePerGas >= baseFee` check trivially passes for any non-negative `maxFeePerGas`.
- `effectiveGasPrice` becomes `0`.
- `gasCost = effectiveGasPrice * gasUsed = 0`, independent of the actual `gasUsed`.

This flows into `DeductGasFeesFromReceipt`: [3](#0-2) 
When `gasCost.Sign() <= 0`, the function returns `nil` (success) without calling `DeductAndBurnFees`. This is invoked from the cache-context-wrapped smart-contract execution path in `ExecuteInboundFundsAndPayload`: [4](#0-3) 
Since `feeErr == nil`, `writeCache()` commits the EVM state changes from `CallExecuteUniversalTx` even though no gas was actually billed — the recipient (or the protocol, since this is module-originated) receives free EVM execution.

This is the same bug class as the ZapMathLib report: a decimal/precision unwrap operation that silently degrades a nonzero quantity to zero because of an unverified assumption about the granularity of a decimal-fixed-point value, corrupting the resulting accounting value used downstream.

### Impact Explanation
This corrupts gas-fee accounting in the universal execution flow (`x/uexecutor`), an explicitly in-scope impact ("corruption of ... gas fee accounting"). A truncated-to-zero gas cost means EVM execution work (potentially unbounded up to the payload's gas limit) is performed without the recipient's balance being debited, effectively minting free compute at the protocol's expense. Impact is Medium: it does not directly drain user funds, but it breaks the fee/accounting invariant that gas consumed must be paid for, and can be repeatedly triggered while the base fee sits at a low value.

### Likelihood Explanation
Low-to-Medium. Triggering requires the feemarket's dynamically-adjusted `BaseFee` to decay to a value whose `LegacyDec` representation is below `1e18` (i.e., below one whole `upc`), which is a function of sustained low block-gas utilization over time rather than a single attacker-controlled transaction. It is not directly attacker-triggerable in one step, but it is reachable purely through ordinary economic/usage conditions without any privileged actor, and every gas computation via this function always rounds the fee down (not just the zero case), meaning at minimum there is a systematic sub-unit revenue leak on every inbound CEA/UEA execution.

### Recommendation
Do not perform integer division to unwrap `LegacyDec` before the price/gas multiplication. Instead, keep the base fee as a `LegacyDec`, multiply by `gasUsed` in `LegacyDec` space, and only convert to an integer `upc` amount (with explicit rounding policy, e.g., `Ceil()` to avoid underbilling) at the very end:
```go
gasCostDec := baseFee.MulInt64(int64(gasUsed))
gasCost := gasCostDec.Ceil().RoundInt().BigInt()
```
This avoids the premature truncation of the base fee itself and ensures `gasCost` is always proportional to `gasUsed`, never spuriously zero.

### Proof of Concept
1. Force (or wait for) the feemarket `BaseFee` param to decay, via sustained periods of blocks below the target gas usage, to a `LegacyDec` value less than `1e18` (e.g., `0.9e18`, i.e., less than 1 whole `upc`).
2. Submit/trigger an inbound CEA payload that resolves to the smart-contract execution path in `ExecuteInboundFundsAndPayload`, invoking `CallExecuteUniversalTx` with a non-trivial `gasUsed`.
3. Observe that `CalculateGasCost` computes `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` = `0`, so `gasCost = 0`.
4. `DeductGasFeesFromReceipt` returns `nil` without calling `DeductAndBurnFees`; `writeCache()` commits the EVM execution with zero fee charged despite non-zero `gasUsed`.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-81)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L250-255)
```go
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
```
