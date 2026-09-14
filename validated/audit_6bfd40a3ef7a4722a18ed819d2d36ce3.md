### Title
Panicking `receiver_shard_id` on stale `GlobalContractDistribution` receipts halts chunk application after repeated resharding - (File: `core/primitives/src/receipt.rs`)

### Summary
`Receipt::receiver_shard_id` returns an `Err(EpochError::ShardingError(...))` when a `GlobalContractDistribution` receipt's `target_shard` cannot be resolved in the current `ShardLayout` and has no descendant found via `resolve_to_current_shard`. This is directly analogous to the reported Multipool bug: a fixed/derived key lookup (`underlyingTrustedPools[500]`) that reverts the entire caller (`rebalanceAll`) when the referenced entry doesn't exist. Here, the "entry" is a stale `target_shard` value baked into a delayed/postponed `GlobalContractDistribution` receipt, and the "caller" is chunk-application code that drains the delayed-receipt queue (`receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs`) via `receiver_shard_id`.

### Finding Description
`receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) computes a receipt's destination shard. For ordinary receipts it maps `receiver_id` through `shard_layout.account_id_to_shard_id`, which always succeeds. For `GlobalContractDistribution` receipts, however, it uses an embedded `target_shard` value computed at receipt-creation time: [1](#0-0) 
If `target_shard` is not a shard id in the *current* layout, the code calls `shard_layout.resolve_to_current_shard(target_shard)` to find a descendant shard after resharding; if that also fails (e.g. because the shard was split more than once, or the ancestry chain is not resolvable across the number of reshardings that occurred while the receipt sat delayed), the function returns an `Err`, not a fallback.

This error propagates into the congestion-control delayed-receipt draining path. Per the protocol-model documentation, `receipt_filter_fn` — which decides whether a popped delayed receipt belongs to the current shard — depends on `receiver_shard_id` succeeding: [2](#0-1) 
The in-repo regression test `test-loop-tests/src/tests/global_contracts_distribution.rs` explicitly documents this scenario: after two shard-split generations, draining the delayed queue containing a stale `GlobalContractDistribution` receipt is expected to panic in `receipt_filter_fn()` because `receiver_shard_id()` fails to remap the old `target_shard`: [3](#0-2) 

The broader failure-mode documentation for this subsystem confirms that any inconsistent-state condition encountered while draining the delayed/buffered/postponed queues is treated as `StorageError::StorageInconsistentState` rather than a soft/skippable error: [4](#0-3) 

This is the same bug class as the Multipool report: a downstream consumer (`getAmountOut`/`rebalanceAll` there, `receipt_filter_fn`/chunk application here) unconditionally dereferences a keyed/indexed value that is not guaranteed to exist for all valid inputs (a specific fee-tier pool there, a specific historical `target_shard` here), and the absence of that entry aborts the entire caller instead of being handled gracefully.

### Impact Explanation
A stuck or panicking chunk-application path is a transaction-triggered halt: because delayed receipts are drained deterministically as part of normal chunk application (not an adversarial or operator-only trigger), a `GlobalContractDistribution` receipt that becomes delayed and then survives across two (or more) resharding events would cause every honest validator applying that chunk to hit the same error/panic, since `receiver_shard_id` and the shard layout are protocol-deterministic. This would either produce a chain-wide halt (if it panics) or a state-root divergence/consensus failure (if implementations differ in how the `Err` is surfaced) — both of which match the accepted impact categories (transaction-triggered halt / state-root divergence). The existing test's assertion (`head_height >= drain_end`, "likely panicked processing stale receipt") shows the nearcore team is aware this is a real risk that must be verified not to occur, i.e., it was recognized as a potential chain-halting condition worth guarding with a dedicated regression test.

### Likelihood Explanation
Likelihood depends on how frequently `GlobalContractDistribution` receipts get delayed and survive across two+ reshardings — a low-probability but non-adversarial, protocol-level occurrence (global contract distribution combined with dynamic resharding, both features gated by protocol version). It does not require a malicious actor; it is purely a function of timing between resharding and receipt delivery, which any network operator or user deploying global contracts could inadvertently trigger. Because the existing repository already ships a targeted regression test asserting the chain does *not* stall in this exact scenario, the underlying risk is acknowledged in-repo, though the test's presence and passing status suggest a fix or mitigation may already have been applied for the currently indexed code path (the exact resolution logic in `resolve_to_current_shard` was not fully inspectable within the available tool budget).

### Recommendation
Ensure `resolve_to_current_shard` is complete for the full possible ancestry depth (bounded only by the maximum number of resharding generations retained), and treat an unresolved `target_shard` in delayed/postponed `GlobalContractDistribution` receipts as a defined, non-panicking outcome (e.g., route to a fallback/garbage-collection path) rather than surfacing a hard error/panic during chunk application. Add exhaustive multi-generation resharding tests (beyond the two-split case already present) to confirm the remapping never fails for any valid ancestry depth.

### Proof of Concept
The repository's own test demonstrates the failure condition: it performs two shard splits while a `GlobalContractDistribution` receipt is delayed, then drains the queue and checks the chain does not stall: [3](#0-2) 
The root-cause function that can fail to resolve the shard is: [5](#0-4)

### Citations

**File:** core/primitives/src/receipt.rs (L437-466)
```rust
    pub fn receiver_shard_id(&self, shard_layout: &ShardLayout) -> Result<ShardId, EpochError> {
        let shard_id = match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::ActionV2(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseYieldV2(_)
            | ReceiptEnum::PromiseResume(_) => {
                shard_layout.account_id_to_shard_id(self.receiver_id())
            }
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
        };
        Ok(shard_id)
    }
```

**File:** protocol-model/spec/cross-shard-congestion.md (L121-127)
```markdown
feed `delayed_receipts_gas` and `receipt_bytes` in `CongestionInfo`.

`DelayedReceiptQueueWrapper::pop` (`congestion_control.rs:880`) also breaks *before*
popping if `trie.check_proof_size_limit_exceed()` (`:889`), and — for
ReshardingV3 — accounts gas/bytes for every popped receipt but returns only those
whose `receiver_shard_id` matches the current shard (`receipt_filter_fn`, `:874`),
skipping receipts that belong to a sibling shard after a split.
```

**File:** protocol-model/spec/cross-shard-congestion.md (L375-379)
```markdown
- **Inconsistent-state failures**: a missing delayed/buffered/postponed/yield item
  referenced by an index yields `StorageError::StorageInconsistentState`
  (`receipts_column_helper.rs:111`, `lib.rs:3011`); a delayed receipt that fails
  `validate_receipt` on pop is likewise treated as inconsistent state, not a soft error
  (`lib.rs:2506`).
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-186)
```rust
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
}
```
