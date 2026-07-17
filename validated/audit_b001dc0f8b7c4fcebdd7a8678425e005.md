### Title
`get_split_parent_shard_ids` Returns Only `last_split` for `ShardLayoutV3`, Causing Buffered Receipts to Become Permanently Stuck After a Second Dynamic Resharding — (`runtime/runtime/src/congestion_control.rs`)

### Summary

Under dynamic resharding (`ProtocolFeature::DynamicResharding`, protocol version 85+), `ShardLayoutV3::get_split_parent_shard_ids()` returns only the **single most-recently-split parent shard** (`last_split`). The congestion-control forwarding loop in `ReceiptSinkV2WithInfo::forward_from_buffer` uses this set to drain buffered receipts that were addressed to a now-split parent shard. After a second resharding event, the first parent shard's ID is evicted from `parent_shard_ids`, and any buffered receipts still addressed to it are never forwarded again — permanently stuck in the trie.

### Finding Description

`ReceiptSinkV2Info::new` populates `parent_shard_ids` by calling `shard_layout.get_split_parent_shard_ids()`: [1](#0-0) 

For `ShardLayoutV3`, that function returns a singleton set containing only `v3.last_split`: [2](#0-1) 

`forward_from_buffer` then iterates over `parent_shard_ids` first, then current shard IDs: [3](#0-2) 

The inner forwarding loop breaks on the first receipt that cannot be forwarded due to congestion limits: [4](#0-3) 

The code itself acknowledges the cleanup is incomplete: [5](#0-4) 

**Concrete scenario:**

1. **Epoch N+2**: Shard A is split into B and C (first dynamic resharding). Stable shard S has buffered receipts addressed to shard A. `parent_shard_ids = {A}`. High congestion prevents full draining — the first `NotForwarded` receipt causes the loop to `break`, leaving the rest of the buffer intact.
2. **Epoch N+3**: Same shard layout; `parent_shard_ids = {A}`. Congestion persists; buffer still not fully drained.
3. **Epoch N+4**: Shard B is split into D and E (second dynamic resharding). The new `ShardLayoutV3` has `last_split = B`, so `get_split_parent_shard_ids()` now returns `{B}`. Shard A is no longer in `parent_shard_ids` and is not in the current shard layout either. The forwarding loop never visits shard A's buffer again. All remaining buffered receipts to shard A are permanently stuck.

The `ShardLayoutV3` cumulative split map (`shards_split_map`) does track the full history, but `get_split_parent_shard_ids` deliberately ignores it and returns only `last_split`: [6](#0-5) 

The test suite confirms `get_split_parent_shard_ids` returns only the most recent split parent, not all historical ones: [7](#0-6) 

### Impact Explanation

Buffered receipts that remain in a stable shard's outgoing buffer addressed to a now-twice-removed parent shard are irrecoverable. The receipts carry NEAR token transfers and/or cross-shard function calls. The sender's tokens are locked inside the receipt forever; the receiver never executes the call. The shard's `own_congestion_info` continues to count the stuck gas/bytes, causing the shard to appear more congested than it actually is, which further throttles legitimate traffic.

### Likelihood Explanation

Dynamic resharding is a nightly/upcoming production feature (protocol version 85). The minimum gap between two reshardings is two epochs (the two-epoch activation delay). Under sustained cross-shard congestion — which is the exact condition that causes receipts to be buffered in the first place — the buffer for the first parent shard may not drain within those two epochs, especially because the forwarding loop breaks on the very first `NotForwarded` receipt, leaving the entire tail of the queue unprocessed. No privileged action is required: normal user transactions create the congestion, and the protocol itself triggers the reshardings.

### Recommendation

`get_split_parent_shard_ids` for `ShardLayoutV3` should return **all** historical parent shards that still have non-empty outgoing buffers, not just `last_split`. One approach: iterate over the full `shards_split_map` (which already stores the complete split history) and return every key whose children are in the current layout. Alternatively, `forward_from_buffer` should consult `ShardsOutgoingReceiptBuffer::shards()` directly to discover any buffer keyed on a shard ID that is absent from the current layout, and drain it regardless of whether it appears in `parent_shard_ids`. The existing `TODO(resharding) - remove the parent outgoing buffer once it's empty` should be resolved as part of this fix.

### Proof of Concept

```
Epoch N+2 shard layout (ShardLayoutV3, last_split = A):
  shards: [B, C, S]          (A was split into B and C; S is stable)
  get_split_parent_shard_ids() → {A}
  forward_from_buffer on shard S: processes buffer[A] → congested, breaks after 0 receipts forwarded

Epoch N+3 shard layout (same, last_split = A):
  get_split_parent_shard_ids() → {A}
  forward_from_buffer on shard S: still congested, buffer[A] still non-empty

Epoch N+4 shard layout (ShardLayoutV3, last_split = B):
  shards: [D, E, C, S]       (B was split into D and E)
  get_split_parent_shard_ids() → {B}   ← A is gone
  forward_from_buffer on shard S:
    parent_shard_ids = {B}   → processes buffer[B] (empty, S never sent to B)
    current shards = {D,E,C,S} → processes buffer[D], buffer[E], buffer[C], buffer[S]
    buffer[A] is NEVER visited → receipts permanently stuck
``` [8](#0-7) [9](#0-8)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L43-47)
```rust
pub(crate) struct ReceiptSinkV2Info {
    epoch_id: EpochId,
    shard_layout: ShardLayout,
    parent_shard_ids: std::collections::BTreeSet<ShardId>,
}
```

**File:** runtime/runtime/src/congestion_control.rs (L222-231)
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
}
```

**File:** runtime/runtime/src/congestion_control.rs (L253-286)
```rust
        let mut all_buffers_empty = true;

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

        // Then forward receipts from the outgoing buffers of the shard in the
        // current shard layout.
        for shard_id in self.info.shard_layout.shard_ids() {
            self.sink.forward_from_buffer_to_shard(
                shard_id,
                state_update,
                apply_state,
                &self.info.shard_layout,
            )?;
            let is_buffer_empty = self.sink.outgoing_buffers.to_shard(shard_id).len() == 0;
            all_buffers_empty &= is_buffer_empty;
        }

        // Assert that empty buffers match zero buffered gas.
        if all_buffers_empty {
            assert_eq!(self.sink.own_congestion_info.buffered_receipts_gas(), 0);
        }

        Ok(())
```

**File:** runtime/runtime/src/congestion_control.rs (L333-337)
```rust
    /// shard if for a short period of time after resharding. That is because
    /// some shards may have receipts for the parent shard that no longer exists
    /// and those receipts need to be forwarded to either of the child shards.
    ///
    /// TODO(resharding) - remove the parent outgoing buffer once it's empty.
```

**File:** runtime/runtime/src/congestion_control.rs (L379-382)
```rust
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
            }
```

**File:** core/primitives/src/shard_layout/mod.rs (L402-424)
```rust
    pub fn get_split_parent_shard_ids(&self) -> BTreeSet<ShardId> {
        // V3 doesn't store shards which weren't split in the map, so we can return early.
        // Using explicit match to force handling a new shard layout version when it's added.
        match self {
            ShardLayout::V0(_) | ShardLayout::V1(_) | ShardLayout::V2(_) => {}
            ShardLayout::V3(v3) => return BTreeSet::from([v3.last_split]),
        }

        let mut parent_shard_ids = BTreeSet::new();
        for shard_id in self.shard_ids() {
            let parent_shard_id = self
                .try_get_parent_shard_id(shard_id)
                .expect("shard_id belongs to the shard layout");
            let Some(parent_shard_id) = parent_shard_id else {
                continue;
            };
            if parent_shard_id == shard_id {
                continue;
            }
            parent_shard_ids.insert(parent_shard_id);
        }
        parent_shard_ids
    }
```

**File:** core/primitives/src/shard_layout/tests.rs (L394-397)
```rust
    assert_eq!(
        derived_layout.get_split_parent_shard_ids(),
        to_shard_ids([3]).into_iter().collect()
    );
```

**File:** core/primitives/src/shard_layout/v3.rs (L343-354)
```rust
    /// Otherwise, return `shard_id`, or `InvalidShardId` error if the shard doesn't exist.
    pub fn try_get_parent_shard_id(&self, shard_id: ShardId) -> Result<ShardId, ShardLayoutError> {
        if !self.shard_ids.contains(&shard_id) {
            return Err(ShardLayoutError::InvalidShardId { shard_id });
        }

        if self.last_split_children().contains(&shard_id) {
            Ok(self.last_split)
        } else {
            Ok(shard_id)
        }
    }
```
