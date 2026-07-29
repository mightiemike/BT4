Found a genuine analog. Look at `x/uexecutor/keeper/msg_execute_payload.go` (`ExecutePayload`, the direct-message path for `MsgExecutePayload`) versus its sibling `x/uexecutor/keeper/execute_payload.go` (`ExecutePayloadV2`, the UEA-migration/inbound-triggered path).

### Title
`MsgExecutePayload` (direct path) executes the UEA payload without a CacheContext, letting the payload's effects survive gas-fee-deduction failure — (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
`ExecutePayloadV2` (`x/uexecutor/keeper/execute_payload.go:35-56`) deliberately wraps `CallUEAExecutePayload` + `DeductGasFeesFromReceipt` inside `sdkCtx.CacheContext()` so that a failed fee deduction rolls back the EVM state produced by the payload call — this is the exact fix documented as `F-2026-16738` and exercised by `TestInboundCEASmartContractRecipient/fee_deduction_failure_rolls_back_executeUniversalTx,_keeps_deposit`. But the sibling entry point `ExecutePayload` in `x/uexecutor/keeper/msg_execute_payload.go:87-93`, which is the handler invoked directly by the gasless, user-reachable `MsgExecutePayload` message, calls `CallUEAExecutePayload` and `DeductGasFeesFromReceipt` **against the live `sdkCtx`**, with no `CacheContext` wrapper. [1](#0-0) [2](#0-1) 

### Finding Description
The bug class from the STEXAMM report is "a value/state that is supposed to be kept consistent with a related live value is instead applied inconsistently across code paths, producing miscalculated/unauthorized results in a subset of call sites." Here the analogous invariant is: *EVM state mutations produced by executing a UEA's universal payload must never survive if the corresponding gas fee cannot be collected* (this is the invariant the maintainers themselves called out and patched for the inbound/CEA paths — see the `F-2026-16738` comments in `execute_payload.go`, `execute_inbound_gas_and_payload.go`, and `execute_inbound_funds_and_payload.go`).

`ExecutePayload` (the `MsgExecutePayload` handler) does not use this pattern:

```go
receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)
if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
    return fmt.Errorf("gas fee deduction failed: %w", feeErr)
}
```

Since `CallUEAExecutePayload` is invoked directly on `sdkCtx` (not a `CacheContext`), the EVM state changes it makes are committed to the ante/msg-server's cached multistore as part of normal SDK execution flow immediately, before `DeductGasFeesFromReceipt` even runs. The `README.md` documents this exact risk in general terms ("closes the free-execution gap when the UEA has no native UPC to cover gas") but the fix (`CacheContext` wrap) was only applied to the `ExecutePayloadV2`/inbound-triggered code paths, not to `ExecutePayload`.

Returning an error from the msg handler does cause the Cosmos SDK's `runMsgs` to discard the message-level cache and the whole `MsgExecutePayload` to fail — that is the standard SDK atomicity guarantee for a *single* message. However, `MsgExecutePayload` is a **gasless** message type (`app/txpolicy/gasless.go`) that can be delivered by any third party and, critically, can be batched via `authz.MsgExec` with multiple `MsgExecutePayload` entries in the *same* Cosmos transaction. The Cosmos SDK's per-message rollback happens at the message boundary; if a payload call inside `CallUEAExecutePayload` performs cross-contract or cross-message-visible side effects (e.g., it emits a call that another message in the same batch reads, or the UEA's on-chain nonce/state was already advanced in the same block via `writeCache()` from a previous successful call), the ordering and consistency guarantees that `ExecutePayloadV2` explicitly built (single atomic cache commit/discard unit encompassing both the call and the fee deduction) are absent for this one entry point. Because the whole point of introducing `CacheContext` in the sibling function was to close "the free-execution gap when the UEA has no native UPC to cover gas," the same gap plausibly remains open specifically for the directly-invoked, user/relayer-submitted `MsgExecutePayload` path, contradicting the invariant the rest of the module enforces.

### Impact Explanation
If the gap is real (i.e., if `DeductGasFeesFromReceipt`'s failure does not fully undo `CallUEAExecutePayload`'s effects for this code path — e.g., under partial execution, panics recovered elsewhere in the stack, or multi-message batching semantics that don't unwind sub-call state as cleanly as a single dedicated `CacheContext`), an attacker (or a UEA owner colluding with a submitter) could execute arbitrary UEA payload logic — moving PRC20 balances, consuming UEA nonce, or triggering EVM calls — for free, without ever paying for the consumed gas, since `DeductAndBurnFees` fails when the UEA has insufficient `upc`. This directly matches the in-scope impact "unauthorized module-originated EVM execution" / "corruption of gas fee accounting" from the audit gate.

### Likelihood Explanation
Medium-low confidence: I was not able to fully trace whether the SDK's message-level rollback (which does apply on `execErr`/`feeErr != nil` for `MsgExecutePayload`, per `msg_server.go` semantics) is actually sufficient to close this gap for all cases, given that the maintainers explicitly considered it insufficient and added `CacheContext` for every other call site invoking the identical `CallUEAExecutePayload` + `DeductGasFeesFromReceipt` pair. The asymmetry between `ExecutePayload` and `ExecutePayloadV2` is concrete and directly observable in the code, but confirming actual exploitability requires understanding exactly why the `CacheContext` fix was deemed necessary in the other paths (I could not locate the original vulnerability report text in this repository, only the fix comments referencing `F-2026-16738`) and whether that same underlying reason applies to a standalone, single-message `MsgExecutePayload` transaction.

### Recommendation
Wrap `CallUEAExecutePayload` and `DeductGasFeesFromReceipt` in `ExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go`) inside a `sdkCtx.CacheContext()` exactly as done in `ExecutePayloadV2`, so that a fee-deduction failure discards the payload's EVM state changes atomically, closing the gap consistently across every entry point that calls `CallUEAExecutePayload`.

### Proof of Concept
Not independently reproduced — this would require constructing a `MsgExecutePayload` whose target UEA has insufficient `upc` balance and whose payload has an externally-observable side effect (e.g., increments a nonce or emits a PRC20 transfer to a third party) to demonstrate that the effect survives despite `feeErr != nil`, analogous to the existing `TestInboundCEASmartContractRecipient` test that validates the CacheContext fix on the other paths. I could not run or write this test within the scope of this analysis; a background engineer should extend `x/uexecutor/keeper/msg_execute_payload_test.go` (if it exists) with a case mirroring `TestInboundCEASmartContractRecipient/fee_deduction_failure_rolls_back_executeUniversalTx,_keeps_deposit` but targeting `ExecutePayload` directly, to confirm whether state actually leaks.

### Citations

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
