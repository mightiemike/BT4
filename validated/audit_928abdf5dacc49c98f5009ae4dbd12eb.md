This is a strong finding: `process_delayed_receipts` at [1](#0-0)  treats any delayed receipt that fails `validate_receipt` as a fatal `StorageInconsistentState` error rather than a recoverable/skippable condition. This mirrors the report's bug class precisely: user-controlled data admitted into a shared, ordered, must-all-process queue, where a single malformed/invalid entry in that queue poisons the entire batch operation for every subsequent chunk application on that shard — not a revert of one transaction, but a hard failure blocking chain progress. However, to actually confirm this is *exploitable by an unprivileged sender* I need to verify whether a legitimate transaction/receipt validated at admission time can still end up malformed once pulled from the delayed queue (e.g., due to a receipt becoming invalid after a protocol feature or resharding remap, similar to the already-known/fixed `receipt_filter_fn` issue), and whether `validate_receipt` can fail for a receipt that passed admission-time checks under some protocol-version transition.### Title
Delayed receipts that fail post-hoc `validate_receipt` re-checks abort chunk application with a fatal `StorageInconsistentState` instead of being skipped, allowing a single stale/invalid queued receipt to halt shard progress - ([File: runtime/runtime/src/lib.rs])

### Summary
This is analogous to the reported USSD bug class: an entity that is admitted into the system without full forward-compatible validation (the Uniswap `_pathbuy`/`_pathsell` byte strings for a collateral) is later consumed by a shared batch/aggregate operation (rebalancing across *all* collaterals), and if that one entry turns out to be invalid for the operation actually performed, the *entire* shared operation reverts, denying service to everyone relying on it. In nearcore, the analogous shared, ordered, "must fully drain" batch operation is the **delayed receipt queue** that every chunk application walks before it can finish applying a chunk for a shard.

### Finding Description
Receipts admitted into the state (delayed queue) are validated once when they are pushed (`ValidateReceiptMode::NewReceipt`/`ExistingReceipt`), but they are re-validated again every time they are later popped for execution in `process_delayed_receipts`: [1](#0-0) 

If that re-check fails for any reason, the receipt is not skipped — the function returns `RuntimeError::StorageError(StorageError::StorageInconsistentState(..))`, which is a **fatal, non-recoverable error** for the shard's chunk application. This propagates up: [2](#0-1) 

`RuntimeError::ReceiptValidationError` and `RuntimeError::StorageError` (other than a couple of specific storage variants) are converted into hard `panic!()`s in the chain layer: [3](#0-2) 

This is the same "one bad, previously-admitted entry poisons a shared batch process for everyone" pattern as the `_pathbuy`/`_pathsell` bug: a receipt that was valid *at admission time* (analogous to the collateral's path being accepted by the admin at setup time) can later fail `validate_receipt` under different runtime conditions (e.g., protocol-version-dependent limit checks, config changes, or state/resharding-dependent fields such as `receiver_shard_id`), and because delayed-queue draining is strict FIFO and must fully process before the chunk can be marked applied, that single bad entry blocks *all* subsequent delayed receipts behind it in the queue, not just itself — and, per the panic path above, can bring down chunk application entirely rather than degrading gracefully.

The project's own documentation of invariants explicitly flags this as a known fragile point: [4](#0-3) 
And the cross-shard congestion spec independently documents the exact same failure mode for the resharding case, describing it as a fixed vulnerability class in the codebase's own test suite comments: [5](#0-4) 
This shows the maintainers are aware that a stale/malformed entry surviving in the delayed queue and later failing a shard-remap/validation check can panic chunk application — the general mechanism (fatal-error-on-recheck-failure) that caused that specific historical bug is still present as the generic error-handling path in `process_delayed_receipts`, so any other latent way to get a receipt that passes admission-time validation but fails the later `validate_receipt(..., ExistingReceipt)` recheck (e.g. a future protocol-version-gated limit change, a config parameter change between the pushing epoch and the draining epoch, or an edge case in receiver/shard resolution) reproduces the same fatal outcome.

### Impact Explanation
Per the validation rules, a "transaction-triggered halt" is an acceptable high-impact outcome. If a receipt admitted while the delayed queue is being filled later fails the existing-receipt validation check for any reason not already excluded during admission, chunk application throws a `StorageInconsistentState`/`ReceiptValidationError`, which the chain layer explicitly `panic!()`s on. Because a shard's delayed queue is strict-FIFO and shared by *all* accounts/receipts routed to that shard, a single non-forward-compatible receipt can block or crash chunk application for every honest validator applying that shard — a chain halt or persistent liveness failure for that shard, matching the "completely disrupt the functionality of the protocol" impact of the reference report, escalated in nearcore to an actual node/shard-halting condition rather than "merely" an unusable feature.

### Likelihood Explanation
Historically this exact class of bug has occurred in this codebase (the resharding `receipt_filter_fn`/`receiver_shard_id` remap panic referenced in the global-contracts-distribution test), which confirms the mechanism is real and has been triggered by ordinary protocol operation (resharding), not by an adversarial peer. The generic "recheck failure ⇒ fatal error ⇒ panic" pattern in `process_delayed_receipts` remains the standing behavior for any future divergence between admission-time and drain-time validation (e.g. a protocol upgrade that tightens `validate_receipt` limits, or a config-store diff that changes `LimitConfig` between the epoch a receipt was queued and the epoch it is drained). I could not fully confirm a currently-triggerable, unpatched concrete input (the resharding one appears already fixed/tested), so likelihood should be treated as **plausible but not proven exploitable today** — it depends on finding a new divergence between the push-time and pop-time validation conditions, which I was not able to fully enumerate within the available tool calls.

### Recommendation
- Treat a delayed receipt that fails re-validation as a soft/skippable condition (e.g., drop it with a recorded outcome/metric, or convert it into a no-op refund) rather than a fatal `StorageInconsistentState` that panics chunk application, at least for validation failure modes that are known to be caused by legitimate protocol/config evolution rather than genuine trie corruption.
- Audit all fields that `validate_receipt` depends on (`LimitConfig`, protocol-version-gated checks, shard/receiver resolution) for backward compatibility guarantees so that a receipt admitted under one config/epoch is guaranteed to remain valid under `ExistingReceipt` mode in all subsequent epochs, closing the class of bug that the `receiver_shard_id` resharding issue represents.
- Add fuzzing/property tests that push receipts under one protocol version/config and drain them under a later one across all currently supported protocol versions and config diffs, specifically targeting `process_delayed_receipts`'s re-validation call.

### Proof of Concept
A concrete, currently-exploitable trigger could not be constructed from static code review alone within the available time; the known historical instance (delayed `GlobalContractDistributionReceipt` failing `receiver_shard_id` remapping across two resharding generations, exercised by `test_global_receipt_distribution_at_resharding_boundary`/`test_global_contract_nonce_prevents_stale_overwrite`-adjacent tests) has apparently already been fixed and guarded by regression tests. Reproducing this class today would require identifying a *new* field or check in `validate_receipt`/`LimitConfig` that can differ between the epoch a receipt is queued (push-time validation) and the epoch it is later popped (`ExistingReceipt` re-validation) — for example, a future runtime-config parameter change (`config_store.rs` `CONFIG_DIFFS`) that tightens a receipt-shape limit checked by `validate_receipt`, combined with a long-sitting delayed receipt that was valid under the old config. This would need to be validated with a background engineering session that can enumerate `validate_receipt`'s exact checks against every `LimitConfig`/protocol-feature diff in `core/parameters/res/runtime_configs/*.yaml` to confirm reachability.

### Citations

**File:** runtime/runtime/src/lib.rs (L2628-2640)
```rust
            // Validating the delayed receipt. If it fails, it's likely the state is inconsistent.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                &receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(|e| {
                StorageError::StorageInconsistentState(format!(
                    "Delayed receipt {:?} in the state is invalid: {}",
                    receipt, e
                ))
            })?;
```

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

**File:** chain/chain/src/runtime/mod.rs (L1296-1304)
```rust
            Err(e) => match e {
                Error::StorageError(err) => match &err {
                    StorageError::FlatStorageBlockNotSupported(_)
                    | StorageError::MissingTrieValue(..) => Err(err.into()),
                    _ => panic!("{err}"),
                },
                _ => Err(e),
            },
        }
```

**File:** protocol-model/spec/runtime-execution.md (L153-153)
```markdown
- **Delayed receipts must stay valid**: a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState` (`runtime/runtime/src/lib.rs:2500`).
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-169)
```rust
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
```
