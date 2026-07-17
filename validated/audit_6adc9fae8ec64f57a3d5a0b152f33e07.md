### Title
`get_child_congestion_info_not_finalized` Asserts `buffered_receipts_gas == 0` for Right Child Using Incomplete `ReceiptGroupsQueue` Metadata, Causing Node Panic at Resharding Boundary — (`chain/chain/src/resharding/manager.rs`)

---

### Summary

During a shard split, `ReshardingManager::get_child_congestion_info_not_finalized` computes the right child's `CongestionInfo` by subtracting buffered-receipt gas/bytes from the parent's `CongestionInfo` using `ReceiptGroupsQueue` metadata. When the outgoing buffer contains receipts that were enqueued before the receipt-group-metadata feature was activated (a window explicitly acknowledged in the codebase), those receipts have no metadata entry. The subtraction loop silently skips them, leaving `buffered_receipts_gas` non-zero. The unconditional `assert_eq!(congestion_info.buffered_receipts_gas(), 0)` then panics, crashing the node mid-resharding.

---

### Finding Description

`get_child_congestion_info_not_finalized` is called for every shard split to derive the right child's initial `CongestionInfo`: [1](#0-0) 

The function starts from the parent's `CongestionInfo` (which carries the full accumulated `buffered_receipts_gas`) and iterates over every shard in the parent layout, loading `ReceiptGroupsQueue` metadata: [2](#0-1) 

If `receipt_groups` is `None` (no metadata exists for that destination shard), the loop body is skipped entirely — no gas or bytes are subtracted. The hard assertion that follows: [3](#0-2) 

will then panic whenever any pre-metadata receipt remains in the parent's outgoing buffer.

The codebase itself documents that this window exists. In `congestion_control.rs`, the bandwidth-request path explicitly handles the case where metadata is incomplete: [4](#0-3) 

That path falls back gracefully. `get_child_congestion_info_not_finalized` has no such fallback — it asserts.

The companion `TODO` in `forward_from_buffer_to_shard` confirms the parent buffer is never cleaned up after it drains: [5](#0-4) 

This means the stale `BufferedReceiptIndices` entries for the parent shard ID persist in the left child's trie indefinitely, compounding the accounting mismatch across future reshardings.

The resharding manager calls `get_child_congestion_info` (which calls `get_child_congestion_info_not_finalized`) for every child shard during `build_split_shard_trie_changes`: [6](#0-5) 

---

### Impact Explanation

A panic inside `build_split_shard_trie_changes` during block processing crashes the node. Because resharding is a consensus-level operation that all tracking validators must execute identically, a panic here halts block production for the affected shard. The node cannot recover without a restart, and if the pre-metadata receipts are still present on restart, the panic recurs. This is a **High** severity finding: it can stall a shard's chunk production until the pre-metadata receipts are fully drained, which may take many blocks under congestion.

---

### Likelihood Explanation

The receipt-group-metadata feature was introduced at protocol version 74 (`_DeprecatedBandwidthScheduler`): [7](#0-6) 

Resharding V3 (SimpleNightshadeV4) was introduced at protocol version 75: [8](#0-7) 

Any shard that had buffered receipts at the v73→v74 boundary and had not fully drained them before the v74→v75 resharding event would trigger the panic. Any user can create buffered receipts by sending cross-shard transactions to a congested shard. The receipts persist until the destination shard's congestion clears. If resharding occurs before that happens, the assertion fires.

---

### Recommendation

Replace the hard `assert_eq!` with the same fallback logic used in `get_receipt_group_sizes_for_buffer_to_shard`: when `ReceiptGroupsQueue` is absent or its `total_receipts_num()` does not match the actual buffer length, bootstrap the missing gas/bytes by iterating the actual `ShardsOutgoingReceiptBuffer` entries directly (as `bootstrap_congestion_info` already does): [9](#0-8) 

Concretely, in `get_child_congestion_info_not_finalized`, after the metadata loop, if `buffered_receipts_gas != 0`, fall back to scanning the actual buffer for the right child (which has no buffered receipts in its retained trie) and zero out the residual. Additionally, implement the deferred cleanup noted in the `TODO`: once the parent-shard-keyed buffer drains to empty, remove its `TrieQueueIndices` entry from `BufferedReceiptIndices` to prevent stale accumulation across successive reshardings.

---

### Proof of Concept

1. At protocol version 73, shard S buffers receipts to shard D (congested). `buffered_receipts_gas > 0` in S's `CongestionInfo`.
2. Protocol upgrades to version 74 (metadata feature enabled). No new receipts are buffered to D, so `ReceiptGroupsQueue` for D remains `None`.
3. Protocol upgrades to version 75 (resharding). Shard S is split into left child L and right child R.
4. `build_split_shard_trie_changes` calls `get_child_congestion_info(…, RetainMode::Right)`.
5. Inside `get_child_congestion_info_not_finalized`: `ReceiptGroupsQueue::load(parent_trie, D)` returns `None`; the loop skips D; `congestion_info.buffered_receipts_gas()` remains equal to the original parent value.
6. `assert_eq!(congestion_info.buffered_receipts_gas(), 0)` — **panics**. Node crashes. [10](#0-9)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L229-236)
```rust
            let child_congestion_info = Self::get_child_congestion_info(
                &parent_trie,
                &parent_shard_layout,
                parent_congestion_info,
                &child_shard_layout,
                new_shard_uid,
                retain_mode,
            )?;
```

**File:** chain/chain/src/resharding/manager.rs (L327-365)
```rust
    fn get_child_congestion_info_not_finalized(
        parent_trie: &dyn TrieAccess,
        parent_shard_layout: &ShardLayout,
        parent_congestion_info: CongestionInfo,
        retain_mode: RetainMode,
    ) -> Result<CongestionInfo, Error> {
        // The left child contains all the delayed and buffered receipts from the
        // parent so it should have identical congestion info.
        if retain_mode == RetainMode::Left {
            return Ok(parent_congestion_info);
        }

        // The right child contains all the delayed receipts from the parent but it
        // has no buffered receipts. It's info needs to be computed by subtracting
        // the parent's buffered receipts from the parent's congestion info.
        let mut congestion_info = parent_congestion_info;
        for shard_id in parent_shard_layout.shard_ids() {
            let receipt_groups = ReceiptGroupsQueue::load(parent_trie, shard_id)?;
            let Some(receipt_groups) = receipt_groups else {
                continue;
            };

            let bytes = receipt_groups.total_size();
            let gas = receipt_groups.total_gas();

            congestion_info
                .remove_buffered_receipt_gas(gas)
                .expect("Buffered gas must not exceed congestion info buffered gas");
            congestion_info
                .remove_receipt_bytes(bytes)
                .expect("Buffered size must not exceed congestion info buffered size");
        }

        // The right child does not inherit any buffered receipts. The
        // congestion info must match this invariant.
        assert_eq!(congestion_info.buffered_receipts_gas(), 0);

        Ok(congestion_info)
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L337-337)
```rust
    /// TODO(resharding) - remove the parent outgoing buffer once it's empty.
```

**File:** runtime/runtime/src/congestion_control.rs (L585-606)
```rust
        // To make a proper bandwidth request we need the metadata for the outgoing buffer to be fully initialized
        // (i.e. contain data about all of the receipts in the outgoing buffer). There is a moment right after the
        // protocol upgrade where the outgoing buffer contains receipts which were buffered in the previous protocol
        // version where metadata was not enabled. Metadata doesn't contain information about them.
        // We can't make a proper request in this case, so we make a basic request while we wait for
        // metadata to become fully initialized. The basic request requests just `max_receipt_size`. This is enough to
        // ensure liveness, as all receipts are smaller than `max_receipt_size`. The resulting behavior is similar
        // to the previous approach where the `allowed_shard` was assigned most of the bandwidth.
        // Over time these old receipts will be removed from the outgoing buffer and eventually metadata will contain
        // information about every receipt in the buffer. From that point on we will be able to make
        // proper bandwidth requests.

        match self.outgoing_metadatas.get_metadata_for_shard(&to_shard) {
            Some(metadata) if metadata.total_receipts_num() == outgoing_receipts_buffer_len => {
                // Metadata fully initialized, use it to read receipt group sizes.
                Box::new(metadata.iter_receipt_group_sizes(trie, side_effects))
            }
            _ => {
                // Metadata not initialized. Make a basic request which requests only `max_receipt_size`.
                Box::new([Ok(params.max_receipt_size)].into_iter())
            }
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L743-788)
```rust
pub fn bootstrap_congestion_info(
    trie: &dyn near_store::TrieAccess,
    config: &RuntimeConfig,
    shard_id: ShardId,
) -> Result<CongestionInfo, StorageError> {
    let mut receipt_bytes: u64 = 0;
    let mut delayed_receipts_gas: u128 = 0;
    let mut buffered_receipts_gas: u128 = 0;

    let delayed_receipt_queue = &DelayedReceiptQueue::load(trie)?;
    for receipt_result in delayed_receipt_queue.iter(trie, true) {
        let receipt = receipt_result?;
        let gas =
            receipt_congestion_gas(&receipt, config).map_err(int_overflow_to_storage_err)?.as_gas();
        delayed_receipts_gas = safe_add_gas_to_u128(delayed_receipts_gas, Gas::from_gas(gas))
            .map_err(int_overflow_to_storage_err)?;

        let memory = receipt_size(&receipt).map_err(int_overflow_to_storage_err)? as u64;
        receipt_bytes = receipt_bytes.checked_add(memory).ok_or_else(overflow_storage_err)?;
    }

    let mut outgoing_buffers = ShardsOutgoingReceiptBuffer::load(trie)?;
    for shard in outgoing_buffers.shards() {
        for receipt_result in outgoing_buffers.to_shard(shard).iter(trie, true) {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, config)
                .map_err(int_overflow_to_storage_err)?
                .as_gas();
            buffered_receipts_gas = safe_add_gas_to_u128(buffered_receipts_gas, Gas::from_gas(gas))
                .map_err(int_overflow_to_storage_err)?;
            let memory = receipt_size(&receipt).map_err(int_overflow_to_storage_err)? as u64;
            receipt_bytes = receipt_bytes.checked_add(memory).ok_or_else(overflow_storage_err)?;
        }
    }

    Ok(CongestionInfo::V1(CongestionInfoV1 {
        delayed_receipts_gas: delayed_receipts_gas as u128,
        buffered_receipts_gas: buffered_receipts_gas as u128,
        receipt_bytes,
        // For the first chunk, set this to the own id.
        // This allows bootstrapping without knowing all other shards.
        // It is also irrelevant, since the bootstrapped value is only used at
        // the start of applying a chunk on this shard. Other shards will only
        // see and act on the first congestion info after that.
        allowed_shard: shard_id.into(),
    }))
```

**File:** core/primitives-core/src/version.rs (L530-531)
```rust
            | ProtocolFeature::_DeprecatedBandwidthScheduler
            | ProtocolFeature::_DeprecatedCurrentEpochStateSync => 74,
```

**File:** core/primitives-core/src/version.rs (L532-532)
```rust
            ProtocolFeature::_DeprecatedSimpleNightshadeV4 => 75,
```
