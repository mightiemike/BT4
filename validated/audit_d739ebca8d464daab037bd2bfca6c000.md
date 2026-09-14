### Title
Unrecoverable panic on `RuntimeError::ReceiptValidationError` / `RuntimeError::UnexpectedIntegerOverflow` in `Runtime::apply` callers causes a deterministic, transaction-triggered chain halt - (File: `chain/chain/src/runtime/mod.rs`)

### Summary
`Runtime::apply` (`runtime/runtime/src/lib.rs:1717`) can return `RuntimeError::ReceiptValidationError` or `RuntimeError::UnexpectedIntegerOverflow` for an incoming/delayed receipt or during receipt processing. The primary chunk-apply caller in the node's block-processing path converts these into a hard `panic!` instead of a recoverable `Error`, exactly the anti-pattern described in the external Solana-poller report (using a panicking check instead of returning/handling the error). Because `apply` is deterministic, any input that reaches this code path will make every validating/tracking node panic on the same receipt at the same height, producing a synchronized chunk-application crash across the network rather than an isolated failure — a transaction/receipt-triggered halt.

### Finding Description
In `chain/chain/src/runtime/mod.rs:349-374`, the wrapper around `Runtime::apply` maps the returned `RuntimeError` as follows: [1](#0-0) 

```
.map_err(|e| match e {
    RuntimeError::InvalidTxError(err) => { ... Error::InvalidTransactions }
    // TODO(#2152): process gracefully
    RuntimeError::UnexpectedIntegerOverflow(reason) => {
        panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
    }
    RuntimeError::StorageError(e) => Error::StorageError(e),
    // TODO(#2152): process gracefully
    RuntimeError::ReceiptValidationError(e) => panic!("{}", e),
    RuntimeError::ValidatorError(e) => e.into(),
})?;
```

Only `InvalidTxError`, `StorageError`, and `ValidatorError` are converted into recoverable `Error` variants; `UnexpectedIntegerOverflow` and `ReceiptValidationError` are turned into hard panics, with an explicit `TODO(#2152): process gracefully` acknowledging this is not the intended long-term behavior.

`RuntimeError::ReceiptValidationError` is documented in `core/primitives/src/errors.rs:70` as occurring when "the incoming receipt didn't pass the validation" and is produced by `validate_receipt` in `runtime/runtime/src/verifier.rs` (e.g. `NumberInputDataDependenciesExceeded`, `ReturnedValueLengthExceeded`, action-validation failures) — this check runs against receipts that arrive at a shard from cross-shard execution or from the delayed-receipt queue, i.e. receipts whose contents are ultimately derived from actions executed on behalf of a user transaction/contract call (per `protocol-model/spec/runtime-execution.md:153`, "a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState`" in some paths, but the top-level `apply` boundary itself still surfaces `ReceiptValidationError`/`UnexpectedIntegerOverflow` as hard `RuntimeError` variants that this caller panics on).

This is the same root-cause pattern flagged in the external report: rather than surfacing an error up the call stack (as is done for `InvalidTxError`/`StorageError`/`ValidatorError`), the code uses `panic!` for two of the five `RuntimeError` variants, with no panic-recovery/circuit-breaker around block/chunk processing to contain the blast radius.

### Impact Explanation
Because `Runtime::apply` is the deterministic state-transition function executed identically by every honest validator/tracking node for a given chunk, any receipt or condition that triggers `ReceiptValidationError` or `UnexpectedIntegerOverflow` will cause **every node that processes that chunk to panic at the same point**, not just one faulty node. Depending on how block processing invokes this code (main thread vs. a supervised task), this can:
- Crash the client process entirely (process-wide DoS / liveness halt), or
- Poison a shared lock/thread if caught only at a coarse boundary, leaving the node in an inconsistent state.

Either way, this is a transaction/receipt-triggered network-wide liveness failure — every validator hits the identical panic and stops making progress on that shard/chunk, which matches the "transaction-triggered halt" impact class explicitly accepted by the validation rules. Both variants are explicitly marked with `// TODO(#2152): process gracefully`, confirming the nearcore team is aware these are not meant to be permanent panics and that they were not intended to be unreachable defensive assertions.

### Likelihood Explanation
Reachability depends on whether an unprivileged actor (transaction sender / contract deployer) can construct a receipt whose `ReceiptValidationError` check fails only at the top-level `apply` boundary rather than being pre-filtered earlier (e.g. by stateless validation before conversion, or by `validate_receipt(..., NewReceipt)` immediately after each new receipt is created inside `apply_action_receipt`, per `runtime/runtime/src/lib.rs:871`). Newly created receipts are validated inline and folded into the action's own `ActionError` (not propagated as a top-level `RuntimeError`), which reduces — but does not eliminate — the surface, since incoming/delayed receipts from other shards or from protocol-version transitions/resharding could still reach the top-level `validate_receipt` check with different validation rules (`ValidateReceiptMode::ExistingReceipt` documents exactly this: "there is a bug which allows to create receipts that are above the size limit... Runtime has to handle them gracefully"). Given the explicit `TODO(#2152)` and the `ExistingReceipt` comment referencing a known receipt-size bug, this is a real, previously-acknowledged reachability path, though I could not fully trace every producer of a cross-shard receipt that would hit this exact panic within the available index (some call sites in `process_incoming_receipts`/`process_delayed_receipts` in `runtime/runtime/src/lib.rs` were not fully retrievable). This uncertainty should be resolved with a live repository session rather than the index.

### Recommendation
- Replace both `panic!` branches in `chain/chain/src/runtime/mod.rs` (`RuntimeError::UnexpectedIntegerOverflow` and `RuntimeError::ReceiptValidationError`) with proper `Error` variants that are handled the same way `StorageError`/`InvalidTxError` are — e.g., mark the chunk/shard as invalid or trigger a controlled resync, rather than crashing the process.
- Resolve the referenced `TODO(#2152)` by auditing every call site that can produce `RuntimeError::ReceiptValidationError` and `RuntimeError::UnexpectedIntegerOverflow` (including cross-shard/delayed receipts and the `ExistingReceipt` legacy-size-bug tolerance path) to confirm whether any of them are reachable from receipts derived from ordinary user transactions/contract calls, and add regression tests analogous to `integration-tests/src/tests/client/invalid_txs.rs::test_invalid_transactions_no_panic` for the receipt path.
- More generally, audit all `panic!`/`.unwrap()`/`.expect()` usages inside `Runtime::apply` and its direct callers (`chain/chain/src/runtime/mod.rs`, `integration-tests/src/user/runtime_user.rs`) for cases reachable from unprivileged, protocol-valid inputs, and convert them to recoverable errors with a supervised recovery/circuit-breaker mechanism at the block-processing boundary.

### Proof of Concept
A definitive, runnable PoC could not be constructed from the indexed code alone: while the panic site itself is clearly located and confirmed (`chain/chain/src/runtime/mod.rs:361-374`), tracing a concrete unprivileged-transaction-derived receipt that reaches this exact `RuntimeError::ReceiptValidationError`/`UnexpectedIntegerOverflow` branch (as opposed to being caught earlier as an `ActionError` inside `apply_action_receipt`) requires reading the full `process_incoming_receipts`/`process_delayed_receipts` implementations in `runtime/runtime/src/lib.rs`, which were not fully available through the search index. A background Devin session with full repository access should verify:
1. Which receipt producers/paths (cross-shard forwarding, delayed queue dequeue, resharding) can surface `ReceiptValidationError`/`UnexpectedIntegerOverflow` as a top-level `RuntimeError` (not pre-caught as `ActionError`).
2. Whether the `ValidateReceiptMode::ExistingReceipt` tolerance for "receipts that are above the size limit" (`runtime/runtime/src/verifier.rs:736-738`, referencing near/nearcore#12606) still allows a legacy-format receipt to hit `NewReceipt`-mode validation on a different shard and trigger the panic.
3. Whether such a receipt can be constructed from a standard `FunctionCall`/`DelegateAction` from an ordinary account.

### Citations

**File:** chain/chain/src/runtime/mod.rs (L361-374)
```rust
            .map_err(|e| match e {
                RuntimeError::InvalidTxError(err) => {
                    tracing::warn!(?err, "invalid tx");
                    Error::InvalidTransactions
                }
                // TODO(#2152): process gracefully
                RuntimeError::UnexpectedIntegerOverflow(reason) => {
                    panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
                }
                RuntimeError::StorageError(e) => Error::StorageError(e),
                // TODO(#2152): process gracefully
                RuntimeError::ReceiptValidationError(e) => panic!("{}", e),
                RuntimeError::ValidatorError(e) => e.into(),
            })?;
```
