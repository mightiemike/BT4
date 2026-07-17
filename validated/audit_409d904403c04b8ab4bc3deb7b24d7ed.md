### Title
`ShardLayoutV3::get_split_parent_shard_ids()` returns only `last_split`, permanently stranding prior-split outgoing receipt buffers after a second dynamic resharding — (`runtime/runtime/src/congestion_control.rs`, `core/primitives/src/shard_layout/mod.rs`)

---

### Summary

Under dynamic resharding (`ShardLayoutV3`), `get_split_parent_shard_ids()` returns only the single most-recently-split parent shard (`{v3.last_split}`). This value is used every chunk application to decide which retired parent outgoing receipt buffers to drain. When a second shard split occurs before the first split's parent buffer is fully drained (due to congestion), the first split's parent buffer is permanently abandoned: `parent_shard_ids` now points to the second split's parent, and the first split's parent buffer is never processed again. Any receipts remaining in that buffer are permanently stuck in the left child's trie and will never be delivered.

---

### Finding Description

**Step 1 — `get_split_parent_shard_ids()` for V3 returns only `last_split`.** [1](#0-0) 

For `ShardLayoutV0/V1/V2`, the function iterates all shards and collects every retired parent. For `ShardLayoutV3` it short-circuits and returns only `{v3.last_split}` — the single shard split in the most recent resharding event. The test suite confirms this: [2](#0-1) 

After a third split (3→5,6), `get_split_parent_shard_ids()` returns `{3}`, not `{0, 2, 3}`.

**Step 2 — `parent_shard_ids` is re-derived from the current epoch's layout on every chunk application.** [3](#0-2) 

`ReceiptSinkV2Info::new()` calls `shard_layout.get_split_parent_shard_ids()` on every chunk. In epoch N+1 (first epoch after shard A splits), `parent_shard_ids = {A}`. After shard B splits in epoch N+2, the new layout has `last_split = B`, so `parent_shard_ids = {B}`.

**Step 3 — Only `parent_shard_ids` buffers are drained.** [4](#0-3) 

`forward_from_buffer` iterates only over `self.info.parent_shard_ids`. After the second split, `parent_shard_ids = {B}`. A1 (left child of A) tries to drain B's parent buffer, but A1 has no buffer for B — the call is a no-op. A's parent buffer in A1 is never iterated again.

**Step 4 — The parent buffer is never cleaned up (acknowledged TODO).** [5](#0-4) 

The `TODO(resharding) - remove the parent outgoing buffer once it's empty` comment confirms the buffer entry in `BufferedReceiptIndices` is never removed. Combined with step 3, any receipts remaining in A's parent buffer after epoch N+1 are permanently stranded in A1's trie.

**Exact divergent value:** `ShardLayoutV3::last_split` is a single `ShardId` field. After the second split it holds B, not A. `get_split_parent_shard_ids()` returns `BTreeSet::from([v3.last_split])` — a set of size 1 that no longer includes A. The `BufferedReceiptIndices` trie key in A1 still contains an entry for A's buffer, but the runtime never reads it again. [6](#0-5) 

---

### Impact Explanation

Receipts remaining in the first split's parent buffer after the second split are permanently undeliverable. These are real cross-shard receipts (function calls, transfers) that were buffered due to congestion. Their receivers will never execute them, meaning:
- Token transfers are lost.
- Cross-contract calls never complete; any promise chains depending on them time out or hang.
- The `own_congestion_info.buffered_receipts_gas` counter in A1 retains the gas/bytes of the stuck receipts, permanently inflating A1's reported congestion level and causing unnecessary backpressure on senders.

**Severity: High.**

---

### Likelihood Explanation

Requires three conditions to coincide:

1. `ProtocolFeature::DynamicResharding` is active (new feature, not yet mainnet but present in production code).
2. Congestion on the child shard prevents the first split's parent buffer from being fully drained within one epoch (~12 hours on mainnet). This is realistic: the `try_forward` loop breaks on the first receipt that exceeds the outgoing limit. [7](#0-6) 

3. A second shard split occurs before the buffer is empty. With `min_epochs_between_resharding = 1` (the minimum enforced value), the second split can take effect in the very next epoch after the first split's parent buffer begins draining.

No privileged role is required; normal network congestion (unprivileged cross-shard traffic) is sufficient to trigger condition 2.

---

### Recommendation

`get_split_parent_shard_ids()` for `ShardLayoutV3` must not short-circuit to `{last_split}`. It should return every retired parent shard that still has a non-empty outgoing buffer in the child's trie, or — more robustly — `forward_from_buffer` should iterate over all shard IDs present in `ShardsOutgoingReceiptBuffer::shards()` rather than relying on `parent_shard_ids` derived from the layout. The existing `TODO(resharding) - remove the parent outgoing buffer once it's empty` should be resolved simultaneously: once a parent buffer is drained, its `BufferedReceiptIndices` entry should be deleted so it is not iterated in future chunks.

---

### Proof of Concept

```
Epoch N-2 (last block): shard A selected for split → embedded in block header.
Epoch N:   new layout V3 with last_split=A takes effect.
           A1 (left child) inherits A's outgoing buffers.
Epoch N+1: forward_from_buffer on A1: parent_shard_ids={A}.
           Child shard C is congested → outgoing_limit[C].size = 0.
           try_forward breaks immediately; 500 receipts remain in A's buffer.
Epoch N+1 (last block): shard B selected for split → embedded in block header.
Epoch N+2: new layout V3 with last_split=B takes effect.
           forward_from_buffer on A1: parent_shard_ids={B}.
           A1 has no buffer for B → no-op.
           A's parent buffer (500 receipts) is never touched again.
           Receivers of those 500 receipts never execute.
```

### Citations

**File:** core/primitives/src/shard_layout/mod.rs (L402-408)
```rust
    pub fn get_split_parent_shard_ids(&self) -> BTreeSet<ShardId> {
        // V3 doesn't store shards which weren't split in the map, so we can return early.
        // Using explicit match to force handling a new shard layout version when it's added.
        match self {
            ShardLayout::V0(_) | ShardLayout::V1(_) | ShardLayout::V2(_) => {}
            ShardLayout::V3(v3) => return BTreeSet::from([v3.last_split]),
        }
```

**File:** core/primitives/src/shard_layout/tests.rs (L394-397)
```rust
    assert_eq!(
        derived_layout.get_split_parent_shard_ids(),
        to_shard_ids([3]).into_iter().collect()
    );
```

**File:** runtime/runtime/src/congestion_control.rs (L222-230)
```rust
impl ReceiptSinkV2Info {
    pub(crate) fn new(
        epoch_id: EpochId,
        epoch_info_provider: &dyn EpochInfoProvider,
    ) -> Result<Self, near_primitives::errors::EpochError> {
        let shard_layout = epoch_info_provider.shard_layout(&epoch_id)?;
        let parent_shard_ids = shard_layout.get_split_parent_shard_ids();
        Ok(ReceiptSinkV2Info { epoch_id, shard_layout, parent_shard_ids })
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L255-266)
```rust
        // First forward any receipts that may still be in the outgoing buffers
        // of the parent shards.
        for &shard_id in &self.info.parent_shard_ids {
            self.sink.forward_from_buffer_to_shard(
                shard_id,
                state_update,
                apply_state,
                &self.info.shard_layout,
            )?;
            let is_buffer_empty = self.sink.outgoing_buffers.to_shard(shard_id).len() == 0;
            all_buffers_empty &= is_buffer_empty;
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L337-337)
```rust
    /// TODO(resharding) - remove the parent outgoing buffer once it's empty.
```

**File:** runtime/runtime/src/congestion_control.rs (L379-381)
```rust
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
```

**File:** core/primitives/src/shard_layout/v3.rs (L115-116)
```rust
    /// The most recent shard split (parent shard ID).
    pub(crate) last_split: ShardId,
```
