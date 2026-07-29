## Analysis [1](#0-0) 

The claim is confirmed, and the underlying bug is actually broader than the question states. `CalculateGasCost` calls `baseFee.BigInt()` and then performs an *additional* division by `1e18`: [2](#0-1) 

`sdkmath.LegacyDec.BigInt()` in the Cosmos SDK already returns the decimal's truncated integer value (i.e., the actual whole-number `upc` amount), not the internal 1e18-scaled fixed-point representation. The comment in the code incorrectly assumes `BigInt()` returns the raw 1e18-scaled internal encoding, so the extra `Div(..., 1e18)` divides the already-correct value a second time. This means `baseFeeBig` only survives as non-zero when the configured base fee is **≥ 1e18 `upc` per gas unit** — an enormous, unrealistic gas price (1 whole PC token per gas unit).

Every genesis/config example in the repo sets base fee far below that threshold (`1000000` or `1000000000` `upc`), and the test harness has to artificially set `baseFee = 1e18` to make `DeductGasFeesFromReceipt` do anything at all: [3](#0-2) 

With any realistic base fee, `CalculateGasCost` returns `effectiveGasPrice = 0`, so `gasCost = 0`, and `DeductGasFeesFromReceipt` early-returns via `gasCost.Sign() <= 0`: [4](#0-3) 

`ExecutePayloadV2` treats this as success and commits the EVM state via `writeCache()` without any fee burn, even though `receipt.GasUsed > 0`: [5](#0-4) 

This is reachable by any ordinary unprivileged user submitting a `MsgExecutePayload`/universal payload through the UEA execution path — no privileged actor or baseFee manipulation is required, since the bug fires for the network's normal/default fee configuration, not just an edge case.

### Title
Double-division bug in `CalculateGasCost` zeroes gas fees for realistic base-fee values, bypassing gas accounting entirely - (`x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` divides the already-integer `baseFee.BigInt()` value by `1e18` a second time, incorrectly assuming `BigInt()` returns a raw 1e18-scaled fixed-point value. This makes `effectiveGasPrice` (and thus `gasCost`) zero for any base fee below `1e18 upc`, which covers all realistic/configured base-fee values in this codebase's genesis and scripts.

### Finding Description
`LegacyDec.BigInt()` already returns the decimal's truncated whole-number value. The code's extra `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` at [6](#0-5)  divides that value again, so only a base fee ≥ `1e18 upc` per gas unit produces a non-zero result. In `DeductGasFeesFromReceipt`, a zero `gasCost` triggers the `gasCost.Sign() <= 0` early return, skipping `DeductAndBurnFees` entirely: [7](#0-6) . `ExecutePayloadV2` then commits the EVM execution state via `writeCache()` regardless, so the UEA's real EVM gas usage is never billed: [8](#0-7) .

### Impact Explanation
Any unprivileged user executing a payload through the universal executor pays zero gas fees for real EVM computation as long as the network's base fee (which is realistically configured at `1e6`–`1e9 upc`, far below the `1e18` threshold) stays below that threshold. This is a systemic corruption of gas fee accounting, allowing free EVM execution paid for by no one, undermining the economic security of the execution layer and enabling resource-consumption spam without cost.

### Likelihood Explanation
High. This is not conditioned on attacker action — it triggers under the chain's normal/default fee-market configuration for every payload execution, as evidenced by every genesis/test script in the repo configuring base fee values (`1e6`, `1e9`) well under `1e18`.

### Recommendation
Remove the redundant division in `CalculateGasCost` — use `baseFee.BigInt()` directly as the upc-denominated base fee, or correct whichever half of the arithmetic is wrong to match one consistent number representation. Add a regression test using a realistic base fee (e.g., `1e6`–`1e9 upc`, matching production genesis values) with `GasUsed > 0` and assert the payer's `upc` balance decreases by the expected `gasCost`.

### Proof of Concept
1. Configure `FeeMarketKeeper` base fee to a realistic production value, e.g. `sdkmath.LegacyNewDec(1_000_000)` (`1e6 upc`, matching `local-multi-validator/scripts/setup-genesis-auto.sh` and `scripts/test_node.sh` defaults).
2. Execute a real payload via `ExecutePayloadV2` with `maxFeePerGas` set appropriately and `GasUsed > 0` from the resulting EVM receipt.
3. Observe `CalculateGasCost` returns `0` because `baseFeeBig.Div(baseFeeBig, 1e18)` truncates `1_000_000 / 1e18` to `0`.
4. Assert the UEA's `upc` balance is unchanged after execution despite `receipt.GasUsed > 0`, confirming gas fees were never deducted or burned.

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

**File:** test/utils/setup_app.go (L151-152)
```go
	baseFee := sdkmath.NewInt(1000000000000000000)                  // Int
	app.FeeMarketKeeper.SetBaseFee(ctx, sdkmath.LegacyDec(baseFee)) // Dec
```

**File:** x/uexecutor/keeper/execute_payload.go (L40-56)
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

	// Both succeeded — commit EVM state and fee deduction together.
	writeCache()
```
