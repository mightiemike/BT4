## Finding

### Title
Truncating division of `baseFee` before multiplication in `CalculateGasCost` systematically under-collects gas fees for UEA/CEA payload execution - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in [1](#0-0)  converts the EIP-1559 `baseFee` (an 18-decimal `sdkmath.LegacyDec`) into a whole-`upc` integer by doing `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` **before** multiplying by `gasUsed`. The inline comment asserts "the base fee is always a whole number of upc -- no fractional upc exists," but this is not enforced anywhere in scoped code — the value comes from the fee market module's EIP-1559-style base-fee adjustment, which is itself a `LegacyDec` computed via multiplication/division against a change-denominator and therefore generically produces a fractional per-gas price. Because the truncation happens on the *per-gas price* rather than on the final fee amount, any fractional part (up to just under 1 upc) is discarded and then effectively multiplied by `gasUsed` when computing `gasCost := effectiveGasPrice * gasUsedBig`, amplifying a bounded rounding error into a potentially large, systematic fee shortfall on every gas-charging call. This is the same root cause as the GMX M-10 report: a fractional remainder from a factor/price computation is silently dropped instead of being tracked, and — unlike the funding-fee case which only loses the "amount rounds to zero" edge case — here the truncation occurs unconditionally on every call, before scaling by size, so the lost value scales with `gasUsed`.

### Finding Description
`DeductGasFeesFromReceipt` is invoked on every EVM-execution-and-fee-deduction path reachable by ordinary users: `MsgExecutePayload` → `ExecutePayloadV2`/`ExecutePayload` [2](#0-1) [3](#0-2) , as well as `ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload` for smart-contract recipients [4](#0-3) [5](#0-4) . Inside `DeductGasFeesFromReceipt`, `baseFee` is pulled straight from `feemarketKeeper.GetBaseFee` and fed into `CalculateGasCost`: [6](#0-5) 

The `Div` truncates any fractional-upc component of the base fee to zero *before* the `Mul` by `gasUsedBig`. If `baseFee` is, for example, `1000000.6` upc/gas and `gasUsed` is `500,000`, the correct charge is `500,000,300,000` upc-equivalent, but the code computes `effectiveGasPrice = 1,000,000` (dropping `0.6`) and charges `500,000,000,000` — a shortfall of `300,000` upc for a single execution, silently absorbed by `DeductAndBurnFees` burning less than the EVM actually consumed.

This is the inverse-but-equivalent flaw to GMX M-10: GMX tracked the "rounds to zero" special case but ignored fractional remainders once the amount was ≥ 1; here, the code assumes the *input* is always an integer and never checks or accumulates the fractional remainder at all, and because the truncation happens on the multiplicand rather than the product, the error is proportional to `gasUsed` rather than bounded to less than one smallest unit.

### Impact Explanation
Every payload execution that burns gas from a UEA (`DeductAndBurnFees`) under-collects by up to `gasUsed × (1 upc − ε)` whenever the current EIP-1559 base fee is not an exact multiple of `1e18` in its `LegacyDec` representation — which is the normal/expected case for a dynamically adjusting base fee, not a rare edge case. This is a systemic under-charging of the protocol's own gas-fee accounting (falls under "corruption of ... gas fee accounting" in the allowed-impact list), reducing burned/collected fees below the true EVM execution cost across the network on an ongoing basis. It does not let an attacker steal external funds directly, but it does corrupt the canonical fee-accounting invariant (`fee charged == effectiveGasPrice × gasUsed`) for every affected block, and does so via ordinary user-submitted `MsgExecutePayload`/inbound execution — no privileged actor is required.

### Likelihood Explanation
High. `GetBaseFee` returns a `LegacyDec` maintained by the standard EIP-1559 base-fee adjustment formula (multiplicative/divisional updates each block based on gas utilization), which will almost never remain an exact multiple of `1e18` after even a single adjustment step. Any transaction that exercises `DeductGasFeesFromReceipt` — which is the default path for `MsgExecutePayload`, a gasless message any account can submit — is affected as soon as the network's base fee drifts off a round `upc` value.

### Recommendation
Do not truncate the per-gas price before multiplying. Instead, compute the full-precision cost first (`baseFee.MulInt64(int64(gasUsed))`, keeping 18-decimal precision throughout) and only round down (or track/carry the fractional remainder analogous to GMX's PR #115 fix) on the final `gasCost` value, so a fractional base fee times a large `gasUsed` cannot compound into a large absolute undercharge. At minimum, remove the unchecked assumption that `baseFee` is always a whole upc value, and add a mechanism to accumulate/settle unclaimed fractional gas-fee remainders instead of silently discarding them every call.

### Proof of Concept
1. Let feemarket adjust `BaseFee` to a non-integer `upc` value, e.g. `1_000_000.6` (`LegacyDec` internally `1000000600000000000000000`).
2. A user submits `MsgExecutePayload` (or an inbound with `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`) whose EVM call consumes `gasUsed = 500_000`.
3. `CalculateGasCost` computes `baseFeeBig.Div(_, 1e18)` → `1_000_000` (drops `0.6`), then `gasCost = 1_000_000 × 500_000 = 500_000_000_000`.
4. The mathematically correct charge is `1_000_000.6 × 500_000 = 500_000_300_000`.
5. `DeductAndBurnFees` burns only `500_000_000_000`, a shortfall of `300_000` upc for this single execution — reproducible deterministically any time `BaseFee`'s fractional-upc component is non-zero, which is the normal case for a dynamically-adjusting base fee.

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

**File:** x/uexecutor/keeper/execute_payload.go (L39-48)
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
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-93)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L250-256)
```go
		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```
