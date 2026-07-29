Based on my research, I did not find a fully qualifying analog that meets the strict "unprivileged attacker, material impact, no privileged assumption" bar defined by the scope. I did find one concrete logic issue worth flagging, but I could not fully verify its reachability under production `feemarket` parameters (this would require inspecting `app/app.go`'s feemarket genesis/params and `x/uexecutor/keeper/keeper_test.go` more closely, which I ran out of iterations to confirm).

### Title
Integer-truncation of base fee in `CalculateGasCost` can make UEA payload execution gas-free - (File: x/uexecutor/keeper/fees.go)

### Summary
`CalculateGasCost` converts the feemarket's `LegacyDec` base fee into a whole-number `upc` price via integer division by `1e18`, discarding any fractional-`upc` remainder. If the network's effective per-gas base fee ever prices out to less than 1 whole `upc` (a plausible state for a low-congestion chain with EIP-1559-style base-fee decay, since `upc` is the atomic 18-decimal unit), the computed `effectiveGasPrice` truncates to `0`, `gasCost` becomes `0`, and `DeductGasFeesFromReceipt` short-circuits with `if gasCost.Sign() <= 0 { return nil }`, skipping fee collection entirely for that payload execution.

### Finding Description
`CalculateGasCost` [1](#0-0)  takes the feemarket base fee (a `LegacyDec`) and does:
```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```
This is a floor division that drops the fractional part of the base fee once it is expressed as a whole `upc` amount. `effectiveGasPrice` is then set directly to this truncated value [2](#0-1) , and `gasCost = effectiveGasPrice * gasUsed`. Downstream, `DeductGasFeesFromReceipt` treats a non-positive `gasCost` as "nothing to charge" and returns `nil` without any deduction [3](#0-2) , allowing `ExecutePayload`/`ExecutePayloadV2` to commit the EVM state changes with zero fee collected [4](#0-3) [5](#0-4) .

### Impact Explanation
If reachable, this would let ordinary `MsgExecutePayload` submitters (an unprivileged, permissionless message type per the gasless/authorization model docs [6](#0-5) ) consume real EVM execution/state at zero cost, undermining gas-fee accounting — the protocol's "hold farming"-equivalent invariant that execution should always be billed unless explicitly gasless. Note that Push Chain's own engineers already hardened the adjacent "fee deduction fails but EVM state committed" gap via `CacheContext` rollback (`F-2026-16738`) [7](#0-6) , showing this exact fee-integrity invariant is treated as security-relevant — but that fix does not address the case where `gasCost` computes to exactly `0` due to truncation, since a zero cost is treated as a legitimate "nothing to charge" outcome, not a failure.

### Likelihood Explanation
Likelihood is **uncertain and likely low** in practice: this requires the feemarket's dynamically-adjusted base fee to price out to a fractional value below 1 whole `upc` (the atomic unit), which depends on `feemarket` module parameters (`MinGasPrice`, base-fee floor, decay rate) that I was not able to fully confirm within available tool budget by reading `app/app.go`'s feemarket configuration and `x/uexecutor/keeper/keeper_test.go`. If the chain's minimum/floor base fee is bounded well above `1e18` (i.e., base fee is always ≥ 1 whole `upc`), this path is unreachable under honest network conditions and the finding would not qualify. Since I could not verify the exact base-fee floor, I cannot assert with confidence that this is attacker-reachable without governance/param manipulation.

### Recommendation
Perform the effective-gas-price calculation using the full `LegacyDec` precision (or an equivalent big-decimal / scaled-integer multiplication) rather than truncating `baseFee` to a whole `upc` integer before multiplying by `gasUsed`, e.g. compute `gasCost = (baseFeeDec.MulInt64(int64(gasUsed))).Ceil()` in `LegacyDec` space and only convert to `big.Int` once, so sub-1-`upc` base fees still accumulate a non-zero, correctly-rounded charge across gas-heavy executions.

### Proof of Concept
Not independently confirmed — would require setting/observing a feemarket base fee whose decimal value is between `0` and `1` `upc` (e.g., via sustained low block-gas utilization driving EIP-1559 base-fee decay toward the module's floor) and then submitting a `MsgExecutePayload` with `gasUsed > 0`; if `feemarketKeeper.GetBaseFee` returns such a value, `CalculateGasCost` returns `0` and `DeductGasFeesFromReceipt` skips billing. I was unable to confirm the concrete minimum base-fee parameter Push Chain ships with, so this PoC is not empirically verified.

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

**File:** x/uexecutor/keeper/fees.go (L72-81)
```go
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

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```
