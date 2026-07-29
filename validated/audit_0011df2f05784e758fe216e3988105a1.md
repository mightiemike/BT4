## Analysis

Found a strong candidate analog in `msg_execute_payload.go`'s `ExecutePayload` function (the non-cached, `MsgExecutePayload` message-server entry point).

### Title
Non-atomic EVM execution and gas-fee deduction in `ExecutePayload` allows free unauthorized UEA execution / permanent gas-fee loss - ([File: x/uexecutor/keeper/msg_execute_payload.go])

### Summary
The APR bug was caused by a routine that performed a state-changing action (increase debt) before verifying there were enough funds to cover the resulting obligation, and the fix was to gate the increase on sufficiency of funds for the following step. The Push Chain analog is in `Keeper.ExecutePayload` (invoked via `MsgExecutePayload`, a **gasless** message type reachable by any unprivileged account, per `app/txpolicy/gasless.go`): it calls `k.CallUEAExecutePayload` (a real, committed `DerivedEVMCall`, i.e. `commit=true`) **before** checking whether the recipient UEA can actually pay for the gas it consumed, and the two steps are **not wrapped in a shared `CacheContext`**.

### Finding Description
`ExecutePayload` in `x/uexecutor/keeper/msg_execute_payload.go`:
```go
receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)   // committed directly on sdkCtx
if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
    return fmt.Errorf("gas fee deduction failed: %w", feeErr)
}
``` [1](#0-0) 

Unlike the sibling helper `ExecutePayloadV2` (used by the inbound-triggered paths), which explicitly wraps both the EVM call and the fee deduction in `sdkCtx.CacheContext()` so they "commit/revert together," `ExecutePayload` calls `CallUEAExecutePayload` directly against `sdkCtx` with `commit=true`, and only afterward attempts to deduct fees: [2](#0-1) 

`DeductGasFeesFromReceipt` deducts based on `gasUsed × effectiveGasPrice` from the recipient UEA's balance, and explicitly documents that it can fail with "insufficient gas" if the balance is too low: [3](#0-2) 

Because `ExecutePayload`'s EVM call is committed on the top-level `sdkCtx` (not a discardable cache context) before the fee check, whether the *whole Cosmos message* ultimately rolls back depends entirely on the outer message-handler transaction machinery treating a returned error as atomic across the full `sdkCtx`, rather than on an explicit, verified commit/rollback boundary local to this function. This is architecturally the same class of bug as the APR report: a value-changing/state-changing operation (real EVM execution against `UNIVERSAL_EXECUTOR_MODULE`-authorized UEA state) is performed before the code verifies that the "following obligation" (paying for the gas that operation consumed) can actually be met, instead of gating the execution on sufficiency of funds first (as `ExecutePayloadV2` does via `CacheContext`).

### Impact Explanation
If the atomicity guarantee here does not hold end-to-end (e.g., under any code path, follow-on refactor, or execution context where `sdkCtx` mutations from `DerivedEVMCall` are not rolled back purely by returning a Go error from the message handler), an attacker with a UEA holding zero/near-zero `upc` balance can trigger real, state-changing `executeUniversalTx` calls (arbitrary payload execution as the UEA) while the gas-fee obligation cannot be collected — i.e., unauthorized module-originated EVM execution with permanent loss of the intended gas-fee accounting. Since `MsgExecutePayload` is in the gasless allowlist, `Signer` need not be funded and any account may submit it for any `UniversalAccountId`, so this is reachable by a fully unprivileged external attacker with no special role.

### Likelihood Explanation
Medium confidence / requires verification. The comment in `execute_payload.go` ("closes the free-execution gap when the UEA has no native UPC to cover gas") indicates the team is aware of exactly this "free execution" class of bug and deliberately patched it in `ExecutePayloadV2` using `CacheContext`. However, `ExecutePayload` (the direct `MsgExecutePayload` path) was not verifiably updated to use the same `CacheContext` pattern in the code retrieved — it relies on the outer Cosmos SDK message-execution rollback instead of a function-local cache/write-back. I could not fully confirm from the available index whether the message-server layer (`msg_server.go` / baseapp) guarantees full-state rollback of `DerivedEVMCall`'s EVM-layer side effects (contract storage, nonces, etc., which live in a different state tree than the Cosmos KV store) on error return from `ExecutePayload`. This is the key open question that determines whether this is exploitable or merely a defense-in-depth inconsistency.

### Recommendation
Wrap `CallUEAExecutePayload` and `DeductGasFeesFromReceipt` in `ExecutePayload` (msg_execute_payload.go) in the same `sdkCtx.CacheContext()` / `writeCache()` pattern already used in `ExecutePayloadV2`, so a failed fee deduction reliably discards the EVM-side state changes, closing any "free execution" gap for underfunded UEAs regardless of how the outer message dispatch handles error returns.

### Proof of Concept
Not independently reproducible from the indexed code alone — reproduction requires confirming in a running node/test harness whether `DerivedEVMCall`'s EVM state effects survive when `ExecutePayload` returns a non-nil error after `CallUEAExecutePayload` succeeds and `DeductGasFeesFromReceipt` fails (i.e., call `MsgExecutePayload` for a UEA with zero `upc` balance and a payload that has an observable side effect, then check whether that side effect persisted despite the returned error).

### Citations

**File:** x/uexecutor/keeper/msg_execute_payload.go (L87-93)
```go
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
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

**File:** x/uexecutor/keeper/fees.go (L93-140)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
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
