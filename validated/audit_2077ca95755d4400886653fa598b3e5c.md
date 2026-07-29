### Title
Integer truncation of `baseFee` in `CalculateGasCost` can zero out gas fee accounting for universal payload execution - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in [1](#0-0)  truncates the EIP‑1559 `baseFee` (a `LegacyDec` with 18‑decimal internal precision) down to a whole `upc` unit via integer `Div` before using it as the effective gas price. This is the same class of bug as the referenced `exp()` report: an approximation/rounding step that is "accurate enough" in the common case but breaks down at small magnitudes, corrupting downstream fee accounting. Here, when the real base fee is below `1.0 upc`, truncation drives `effectiveGasPrice` to `0`, and `DeductGasFeesFromReceipt` explicitly treats non‑positive gas cost as "nothing to charge," letting real EVM execution proceed with zero fee collected.

### Finding Description
`CalculateGasCost` computes:
```go
baseFeeBig := baseFee.BigInt()          // 18-decimal fixed-point internal repr
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // integer division, floors toward zero
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
``` [2](#0-1) 

The comment asserts "the base fee is always a whole number of upc -- no fractional upc exists" [3](#0-2) , but this is an unenforced assumption about the fee‑market base fee, which is dynamically adjusted (EIP‑1559 style) as a `LegacyDec` and is not guaranteed to be an exact integer number of `upc`. Whenever the base fee value drops below `1.0 upc` (e.g. during sustained low network utilization, on a low‑traffic testnet, or if governance sets a low starting/min value), `baseFeeBig.Div(..., 1e18)` floors to `0`.

The downstream check in `DeductGasFeesFromReceipt` only rejects when `maxFeePerGas < baseFeeBig` [4](#0-3) ; with `baseFeeBig == 0` this check is vacuous (any non‑negative `maxFeePerGas` passes). It then computes `gasCost = 0 * gasUsed = 0` and short‑circuits:
```go
if gasCost.Sign() <= 0 {
    return nil
}
``` [5](#0-4) 

This is invoked from `ExecutePayloadV2`, the standard, unprivileged, user‑reachable universal payload execution path (any external caller with a resolvable UEA can trigger it) [6](#0-5) . The comment in that function even acknowledges the underlying concern: "closes the free-execution gap when the UEA has no native UPC to cover gas" [7](#0-6)  — but that protection only covers the case of an EVM-state/fee-deduction atomicity failure, not the case where the computed fee is silently zero because of truncation.

### Impact Explanation
When `baseFee` is below `1 upc`, real EVM execution (`CallUEAExecutePayload`, consuming actual `gasUsed`) is billed `0` gas fee, while validators/the network still incur the real compute cost. This corrupts gas fee accounting invariants (an explicitly in-scope impact area: "corruption of ... gas fee accounting ... revert destination ... or canonical UniversalTx state") and lets any unprivileged user execute arbitrary UEA payloads for free, with no burn/fee collection occurring despite genuine gas consumption. Even at base fees near but above `1 upc`, the same floor-division systematically undercharges the effective gas price (loses the fractional `upc` component every single payload execution), a smaller but still real accounting-corruption effect consistent with the referenced report's approximation-error theme.

### Likelihood Explanation
This does not require any privileged action or validator collusion — it is triggered purely by the natural/expected value range of a market-driven `LegacyDec` base fee combined with an unchecked truncation assumption in the module's own code. Any period of low network gas demand (which drives EIP‑1559-style base fee down over blocks) or any deliberately low base-fee configuration (e.g., early mainnet/testnet bootstrap, governance-set low starting fee) can push the value below `1 upc`, at which point the bug is trivially and repeatedly exploitable by ordinary users submitting `ExecutePayloadV2` calls.

### Recommendation
Do not truncate `baseFee` to whole `upc` before multiplying by `gasUsed`. Instead:
- Keep the effective gas price as a `LegacyDec` (or scaled `big.Int`) through the multiplication with `gasUsed`, and only round (ceiling, not floor) the *final* gas cost to a whole `upc` amount, matching the ceiling-based approach already used in `MinGasPriceDecorator`/`checkTxFeeWithValidatorMinGasPrices` (`fee.Ceil().RoundInt()`), or otherwise guarantee `gasCost > 0` whenever `baseFee > 0` and `gasUsed > 0`.
- Remove/replace the "base fee is always whole upc" assumption with an explicit invariant check, and add a lower bound / floor param on the fee-market's base fee if a zero-fee state is never intended.
- Add a unit test asserting that a sub-1-`upc` base fee combined with non-zero `gasUsed` still produces a strictly positive `gasCost`.

### Proof of Concept
1. Configure/observe the fee-market such that `GetBaseFee` returns a `LegacyDec` less than `1.000000000000000000` (e.g., `0.900000000000000000`), which is reachable through normal EIP‑1559-style base-fee decay during sustained low block utilization, or via test/genesis configuration.
2. Any unprivileged user submits a message that results in `ExecutePayloadV2` being called with a `UniversalPayload` whose `MaxFeePerGas` is any non-negative value (trivially satisfied since `baseFeeBig` truncates to `0`, so the `maxFeePerGas < baseFeeBig` guard never fires).
3. The EVM call in `CallUEAExecutePayload` executes and consumes real `gasUsed > 0`.
4. `CalculateGasCost` computes `baseFeeBig.Div(baseFeeBig, 1e18) == 0` → `effectiveGasPrice == 0` → `gasCost = 0 * gasUsed = 0`.
5. `DeductGasFeesFromReceipt` hits `if gasCost.Sign() <= 0 { return nil }` and returns without calling `DeductAndBurnFees`, so no funds are transferred/burned from the recipient despite genuine gas consumption [5](#0-4) .

Note: I was unable to inspect the exact base-fee adjustment algorithm implementation (it lives in the external `cosmos/evm` `x/feemarket` dependency, not in this repository's indexed code), so I could not directly confirm from source how quickly/frequently the base fee decays below `1 upc` under real network conditions; this should be verified in a live/test environment by a Devin session with full repository and dependency access.

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

**File:** x/uexecutor/keeper/execute_payload.go (L17-53)
```go
func (k Keeper) ExecutePayloadV2(ctx context.Context, evmFrom common.Address, ueaAddr common.Address, universalPayload *types.UniversalPayload, verificationData string) (*vmtypes.MsgEthereumTxResponse, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Debug("execute payload v2",
		"uea", ueaAddr.Hex(),
		"from", evmFrom.Hex(),
	)

	// Step 1: Validate payload and verificationData early (fast-fail before EVM work)
	if _, err := types.NewAbiUniversalPayload(universalPayload); err != nil {
		return nil, errors.Wrapf(err, "invalid universal payload")
	}

	verificationDataVal, err := utils.HexToBytes(verificationData)
	if err != nil {
		return nil, errors.Wrapf(err, "invalid verificationData format")
	}

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
```
