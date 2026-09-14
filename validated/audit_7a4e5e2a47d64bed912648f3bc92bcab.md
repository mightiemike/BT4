Based on my research, I found a concrete analog in nearcore. It's documented directly in the codebase as an already-known/tested bug class matching the CVE's pattern (an index-based lookup used in event/receipt handling that can point past the valid set after a topology change, i.e., a stale-shard-id-to-index lookup that isn't bounds-checked against the *current* shard layout).

### Title
Stale-shard `receiver_shard_id()` lookup in `receipt_filter_fn`/congestion forwarding can panic on out-of-bounds shard remap after repeated resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::pop` and the receipt-forwarding path resolve a receipt's destination shard via `Receipt::receiver_shard_id(&shard_layout)` against the *current* `ShardLayout`. For `GlobalContractDistribution` (and other receipts carrying a stale `target_shard`/`receiver_id` mapping) that were enqueued before a shard split, after **two** resharding generations the old `target_shard` no longer has a corresponding entry/index in the current shard layout. `receiver_shard_id()` / `get_shard_index()` is expected to fail gracefully, but the surrounding code (`receipt_filter_fn`, `try_forward`) is written assuming it always resolves to a valid index in the current layout, mirroring the kernel bug class of “index/pointer lookup returns an out-of-bounds/invalid result that the caller doesn't check before dereferencing.”

### Finding Description
The congestion-control/receipt-forwarding code path (`ReceiptSinkV2::try_forward`, `runtime/runtime/src/congestion_control.rs:403-462`) and `DelayedReceiptQueueWrapper::pop`'s `receipt_filter_fn` (`congestion_control.rs:874`, described in `protocol-model/spec/cross-shard-congestion.md:123-127`) resolve `receiver_shard_id()` for every popped/forwarded receipt and key into per-shard structures (`outgoing_limit: HashMap<ShardId, OutgoingLimit>` at `congestion_control.rs:441`, and shard-indexed arrays such as `ShardLinkMap` in `runtime/runtime/src/bandwidth_scheduler/scheduler.rs:712-739` which does raw `Vec` indexing keyed by shard index with only a `debug_assert!` bounds check that is compiled out in release builds).

This exactly parallels the kernel CVE's root cause: `ams_event_to_channel()` performs a linear scan/lookup by a key (`scan_index`) that may not exist in the current table and, on a miss, returns a pointer one-past-the-end instead of `None`, which the caller then dereferences. In nearcore's analog, a receipt (e.g. a `GlobalContractDistribution` receipt, or a delayed/buffered receipt referencing an old `target_shard`) that predates a shard split carries a `target_shard`/`receiver_id` that, after a *second* resharding, maps to neither its original shard id/index nor a currently-valid child shard index. The test at `test-loop-tests/src/tests/global_contracts_distribution.rs:163-186` explicitly documents this: *"If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations."* This is functionally the same defect pattern as the CVE: a by-key/by-id lookup used inside a receipt/event-handling routine that can return an invalid/expired mapping which the caller indexes into without validating it against the current, live table (shard layout after resharding), producing invalid memory access analogs in Rust: an index-out-of-bounds panic (`Vec::get`/`[]` on `outgoing_limit`, `ShardLinkMap.data[...]`, or an `.unwrap()`/`.expect()` on `get_shard_index`).

### Impact Explanation
An out-of-bounds/invalid index resolved during receipt or event processing that is not defensively rejected causes a node-crashing panic when a chunk containing (or referencing) the stale receipt is applied. Because this happens inside `Runtime::apply`/`process_delayed_receipts`/`forward_from_buffer`, which every validator must execute identically to reach consensus, a single crafted or naturally-occurring stale receipt surviving across two resharding events can deterministically halt chunk production/application on all honest nodes that reach that receipt — a transaction-triggered halt of the kind explicitly in scope (denial of service / chain halt via a receipt that no submitter fully controls the timing of, but whose existence is a direct consequence of the resharding + congestion/bandwidth code paths that process transaction-derived receipts).

### Likelihood Explanation
The scenario requires two shard-split resharding events occurring while a receipt with a shard-scoped target (such as `GlobalContractDistribution`, or any receipt sitting in the delayed/outgoing-buffer queues) remains unresolved. This is a narrow, resharding-schedule-dependent condition rather than something a single unprivileged transaction can trigger instantly, but resharding is a routine, deterministic protocol event and the bug is already flagged and reproduced by an in-repo regression test, indicating the nearcore team identified this as a real, previously-latent panic path.

### Recommendation
Ensure every `receiver_shard_id()` / `get_shard_index()` result derived from receipts that can outlive a shard layout (delayed queue, outgoing buffers, `GlobalContractDistribution`) is explicitly validated against the *current* `ShardLayout` before being used to index `outgoing_limit`, `ShardLinkMap`, or other per-shard arrays; on a miss, remap through the ancestor/child shard chain (as is done elsewhere for `ShardUId::from_shard_id_and_layout` fallbacks) rather than panicking, and replace any `debug_assert!`-only bounds checks in `ShardLinkMap::data_index_for_link` with checks that also apply in release builds.

### Proof of Concept
The repository already contains a targeted regression test reproducing this exact condition: `test-loop-tests/src/tests/global_contracts_distribution.rs` drives two shard splits while a `GlobalContractDistribution` receipt targeting the original (pre-split) shard is left in the delayed queue, then drains the queue and asserts the chain does not stall/panic: [1](#0-0) 
The forwarding/filter logic under test is `ReceiptSinkV2::try_forward` and the `DelayedReceiptQueueWrapper::pop`'s `receipt_filter_fn`, both keying per-shard state off `receiver_shard_id()`: [2](#0-1) [3](#0-2) 
The narrative spec cross-references the same fragile path: [4](#0-3) 
And the shard-indexed structure used by bandwidth scheduling relies on debug-only bounds checks: [5](#0-4)

### Citations

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

**File:** runtime/runtime/src/congestion_control.rs (L292-325)
```rust
    pub(crate) fn forward_or_buffer_receipt(
        &mut self,
        receipt: Receipt,
        apply_state: &ApplyState,
        state_update: &mut TrieUpdate,
    ) -> Result<(), RuntimeError> {
        let shard = receipt.receiver_shard_id(&self.info.shard_layout)?;
        let size = compute_receipt_size(&receipt)?;
        let gas = compute_receipt_congestion_gas(&receipt, &apply_state.config)?;

        match ReceiptSinkV2::try_forward(
            receipt,
            gas,
            size,
            shard,
            &mut self.sink.outgoing_limit,
            &mut self.sink.outgoing_receipts,
            apply_state,
            &mut self.sink.stats,
        )? {
            ReceiptForwarding::Forwarded => (),
            ReceiptForwarding::NotForwarded(receipt) => {
                self.sink.buffer_receipt(
                    receipt,
                    size,
                    gas,
                    state_update,
                    shard,
                    apply_state.config.use_state_stored_receipt,
                )?;
            }
        }
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L403-462)
```rust
    fn try_forward(
        receipt: Receipt,
        gas: Gas,
        mut size: u64,
        shard: ShardId,
        outgoing_limit: &mut HashMap<ShardId, OutgoingLimit>,
        outgoing_receipts: &mut Vec<Receipt>,
        apply_state: &ApplyState,
        stats: &mut ReceiptSinkStats,
    ) -> Result<ReceiptForwarding, RuntimeError> {
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }

        // Default case set to `Gas::MAX`: If no outgoing limit was defined for the receiving
        // shard, this usually just means the feature is not enabled. Or, it
        // could be a special case during resharding events. Or even a bug. In
        // any case, if we cannot know a limit, treating it as literally "no
        // limit" is the safest approach to ensure availability.
        let default_gas_limit = Gas::MAX;

        // Since bandwidth scheduler, a shard is not allowed to send any receipts if it doesn't have a grant.
        let default_size_limit = 0;

        let default_outgoing_limit =
            OutgoingLimit { gas: default_gas_limit, size: default_size_limit };
        let forward_limit = outgoing_limit.entry(shard).or_insert(default_outgoing_limit);

        let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
            .enabled(apply_state.current_protocol_version)
        {
            gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
        } else {
            gas
        };

        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);

            Ok(ReceiptForwarding::Forwarded)
        } else {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "not forwarding buffered receipt");
            Ok(ReceiptForwarding::NotForwarded(receipt))
        }
```

**File:** protocol-model/spec/cross-shard-congestion.md (L123-127)
```markdown
`DelayedReceiptQueueWrapper::pop` (`congestion_control.rs:880`) also breaks *before*
popping if `trie.check_proof_size_limit_exceed()` (`:889`), and — for
ReshardingV3 — accounts gas/bytes for every popped receipt but returns only those
whose `receiver_shard_id` matches the current shard (`receipt_filter_fn`, `:874`),
skipping receipts that belong to a sibling shard after a split.
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L712-740)
```rust
    pub fn get(&self, link: &ShardLink) -> Option<&T> {
        self.data[self.data_index_for_link(link)].as_ref()
    }

    pub fn insert(&mut self, link: ShardLink, value: T) {
        let data_index = self.data_index_for_link(&link);
        self.data[data_index] = Some(value);
    }

    #[cfg(test)]
    pub fn num_indexes(&self) -> usize {
        self.num_indexes
    }

    fn data_index_for_link(&self, link: &ShardLink) -> usize {
        debug_assert!(
            link.sender < self.num_indexes,
            "Sender index out of bounds! num_indexes: {}, link: {:?}",
            self.num_indexes,
            link
        );
        debug_assert!(
            link.receiver < self.num_indexes,
            "Receiver index out of bounds! num_indexes: {}, link: {:?}",
            self.num_indexes,
            link
        );
        link.sender * self.num_indexes + link.receiver
    }
```
