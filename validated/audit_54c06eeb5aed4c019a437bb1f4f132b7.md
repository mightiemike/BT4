I found the key confirming evidence.

### Title
Transaction-triggered node panic on `RuntimeError::UnexpectedIntegerOverflow` in `apply_chunk` - ([File: chain/chain/src/runtime/mod.rs])

### Summary
The Nibbl `_updateTwav()`/`_getTwav()` finding is about an *unchecked* cumulative accumulator that, once it overflows, causes every subsequent contract call touching it to revert, permanently bricking that function. nearcore's cumulative accumulators (gas price, congestion info, storage usage, gas counters) are *not* naively unchecked like Nibbl's — they consistently use `checked_add`/`checked_sub` and propagate an error instead of wrapping. However, the way the top-level chunk-application code handles that error is the closest structural analog to "an arithmetic condition that should be handled gracefully instead crashes the system": `RuntimeError::UnexpectedIntegerOverflow` returned from `Runtime::apply` is caught explicitly and turned into an unconditional `panic!` in the real (non-test) code path.

### Finding Description
In `chain/chain/src/runtime/mod.rs`, the production chunk-application path maps `Runtime::apply`'s `Result` as follows: [1](#0-0) 

```
RuntimeError::UnexpectedIntegerOverflow(reason) => {
    panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
}
```//with the comment `// TODO(#2152): process gracefully` directly above it.

This error is returned from many checked-arithmetic call sites inside `runtime/runtime/src/lib.rs` and its submodules whenever a `checked_add`/`checked_sub`/`checked_mul` over a runtime-visible cumulative quantity fails — e.g. total prepaid gas across a receipt's actions, total deposit, gas refund computations, congestion-info gas accounting (`add_delayed_receipt_gas`, `add_buffered_receipt_gas` in `core/primitives/src/congestion_info.rs`), and global-contract nonce increments (`runtime/runtime/src/global_contracts.rs`). All of these paths are reachable from ordinary user-submitted actions batched into a receipt (e.g., many `FunctionCall` actions each carrying large `gas`/`deposit` values, or many actions summed via `total_prepaid_gas`/`total_deposit`), since these are `u128`/`u64` sums over attacker-controlled per-action values in a single transaction/receipt.

Where the Nibbl bug makes the *contract* permanently revert once its accumulator overflows, the nearcore analog makes the *entire validating/producing node process* panic (crash) once such an overflow condition is hit during `apply_chunk`, because the error handler is `panic!` rather than a recoverable `Result`.

### Impact Explanation
A panic inside `apply_chunk` (called from block/chunk application in the client) crashes the node process handling that shard. If a single crafted transaction/receipt can be constructed so that a `checked_*` arithmetic call inside the apply path returns `None` (e.g., an extremely large sum of `deposit` or `gas` fields across many actions overflowing `u128`/`u64`), then any honest validator/RPC node that attempts to apply the chunk containing it will panic and crash. If this is deterministic across all validators tracking the shard (which it would be, since the arithmetic is over the same receipt data everywhere), it constitutes a transaction-triggered halt of chunk production for that shard — a liveness/denial-of-service impact reachable by an unprivileged transaction submitter.

### Likelihood Explanation
Likelihood is limited by the same practical bound noted in the original Nibbl judge's decreased-severity ruling: reaching `u128`/`u64` overflow via legitimate protocol quantities (`Balance`, `Gas`) requires values that are validated/bounded elsewhere (`validate_actions`, `total_deposit`/`total_prepaid_gas` limits, action count/size limits in `LimitConfig`). Most of the checked-arithmetic sites reachable from a single transaction are guarded upstream by `ActionsValidationError` checks (e.g., max total prepaid gas, max actions per transaction, max deposit magnitude), which is why this is presented as a "hint" needing verification against current `LimitConfig`/`VMLimitConfig` bounds rather than an unconditionally exploitable path today. It is included because the *pattern* — a checked-overflow condition being escalated to `panic!` instead of a recoverable error in the mainline `apply_chunk` code, explicitly marked `TODO(#2152): process gracefully` — is a live design gap analogous in spirit to the reported bug class (an arithmetic edge case that should degrade gracefully instead permanently disables normal operation).

### Recommendation
Replace the `panic!` on `RuntimeError::UnexpectedIntegerOverflow` in `chain/chain/src/runtime/mod.rs` (and any other production call sites doing the same) with a recoverable error path (e.g., treat as an invalid chunk/invalid transaction and reject it) instead of crashing the node, and audit all `checked_add`/`checked_sub`/`checked_mul` call sites reachable from a single transaction/receipt (`total_prepaid_gas`, `total_deposit`, `total_prepaid_exec_fees`, congestion-info gas accounting) to confirm they are unreachable from attacker-controlled magnitudes given current `LimitConfig` bounds; add regression tests that attempt maximal-magnitude actions/receipts to confirm no panic path is reachable via a single transaction.

### Proof of Concept
Not independently verified against current `LimitConfig`/`VMLimitConfig` bounds; requires confirming whether any single transaction/receipt (bounded by `max_number_bytes`, `max_total_prepaid_gas`, `max_actions_per_receipt`, etc.) can drive one of the `checked_*` sums in `runtime/runtime/src/lib.rs` (`total_prepaid_gas`, `total_deposit`) or `core/primitives/src/congestion_info.rs` accumulators to overflow, thereby returning `RuntimeError::UnexpectedIntegerOverflow` and triggering the `panic!` at [2](#0-1) . A Devin session should construct such a transaction/receipt with a `TestEnv` and current genesis config to confirm reachability before treating this as more than a design-gap finding.

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
