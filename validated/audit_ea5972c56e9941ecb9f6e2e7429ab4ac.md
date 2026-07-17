### Title
Child `ChunkExtra.proposed_split` Inherits Parent's Split Proposal Across Shard Resharding Boundary — (`File: chain/chain/src/resharding/manager.rs`)

### Summary

When a shard is split into two children during dynamic resharding, the child `ChunkExtra` is created by cloning the parent's `ChunkExtra` without resetting the `proposed_split` field. The children therefore inherit the parent's non-`None` split proposal. The first chunk application on each child independently recomputes `proposed_split = None` (the child is a fresh shard, below any threshold), producing a mismatch between the stored `ChunkExtra.proposed_split` and the chunk header's `proposed_split`, which triggers `InvalidChunkHeaderShardSplit` and stalls the chain.

### Finding Description

In `process_memtrie_resharding_storage_update` the child `ChunkExtra` is seeded by a verbatim clone of the parent:

```rust
// chain/chain/src/resharding/manager.rs  lines 258-260
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
```

Only `state_root` and `congestion_info` are overwritten. The `proposed_split: Option<TrieSplit>` field — which the parent's final chunk application set to `Some(TrieSplit { boundary_account, left_mem, right_mem })` — is silently carried into both children. [1](#0-0) 

The `ChunkExtraV5` struct that holds this field is: [2](#0-1) 

The chunk-header validation invariant requires that the `proposed_split` embedded in an incoming `ShardChunkHeaderInnerV5` exactly matches the `proposed_split` stored in the previous `ChunkExtra` for that shard: [3](#0-2) 

For the first chunk of each child shard, `compute_proposed_split` returns `None` (the child is new, its memory usage is below any threshold, and the resharding cooldown has not elapsed). The chunk header therefore carries `proposed_split = None`. But the stored `ChunkExtra` for that child — seeded from the parent — carries `proposed_split = Some(...)`. The validator comparing the two sees a divergence and rejects the chunk with `InvalidChunkHeaderShardSplit`.

The codebase itself documents this exact failure mode and the workaround:

> `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`. [4](#0-3) 

A companion TODO in the same resharding function acknowledges the incomplete field initialisation:

> `// TODO(resharding): set all fields of ChunkExtra. Consider stronger typing.` [5](#0-4) 

### Impact Explanation

If `min_epochs_between_resharding` is configured to `0` — a value the `DynamicReshardingConfig` struct does not prohibit at the type level — the cooldown gate in both `compute_proposed_split` and `get_upcoming_shard_split` passes immediately after a split. The next epoch's child shards then carry a stale `proposed_split` in their `ChunkExtra`. Every chunk producer for those shards will produce a chunk header with `proposed_split = None`, which fails the stored-vs-header comparison. The chain cannot accept any chunk for those shards, causing a permanent stall for all accounts in the affected shards. [6](#0-5) [7](#0-6) 

### Likelihood Explanation

The production guard is a runtime configuration value (`min_epochs_between_resharding > 0`), not a compile-time or type-level invariant. Any operator who sets this to `0` — whether intentionally for testing or by misconfiguration — triggers the bug deterministically on the first resharding. Because dynamic resharding is a new feature (`ProtocolFeature::DynamicResharding`) whose configuration is still being tuned, the risk of an incorrect value reaching a live network is non-trivial.

### Recommendation

Explicitly reset `proposed_split` to `None` in the child `ChunkExtra` immediately after cloning the parent, so the stored value always matches what the first chunk application will compute:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Reset: child shards have no pending split proposal at birth.
*child_chunk_extra.proposed_split_mut() = None;
```

This removes the dependency on the `min_epochs_between_resharding > 0` invariant being upheld externally and closes the gap noted in the existing TODO.

### Proof of Concept

1. Configure `DynamicReshardingConfig` with `min_epochs_between_resharding = 0` and `force_split_shards` containing shard 0.
2. Run the chain until epoch N ends; `finalize_epoch` embeds `shard_split = Some((0, boundary))` in the last block header and creates `EpochInfoV5` for epoch N+2 with the new layout.
3. `process_memtrie_resharding_storage_update` clones the parent `ChunkExtra` (which has `proposed_split = Some(TrieSplit { ... })`) into both children and saves them.
4. In epoch N+1, the first chunk application for each child calls `compute_proposed_split`; `can_reshard` returns `true` (cooldown = 0), but the child's trie is below threshold, so it returns `None`. The chunk header is produced with `proposed_split = None`.
5. Chunk validation compares `chunk_header.proposed_split() == None` against `prev_chunk_extra.proposed_split() == Some(...)` — mismatch → `InvalidChunkHeaderShardSplit` → chunk rejected → chain stall for all accounts in the split shards.

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-260)
```rust
            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;
```

**File:** core/primitives/src/types.rs (L879-900)
```rust
    /// V4 -> V5: add proposed_split (dynamic resharding)
    #[derive(Debug, PartialEq, BorshSerialize, BorshDeserialize, Clone, Eq, serde::Serialize)]
    pub struct ChunkExtraV5 {
        /// Post state root after applying give chunk.
        pub state_root: StateRoot,
        /// Root of merklizing results of receipts (transactions) execution.
        pub outcome_root: CryptoHash,
        /// Validator proposals produced by given chunk.
        pub validator_proposals: Vec<ValidatorStake>,
        /// Actually how much gas were used.
        pub gas_used: Gas,
        /// Gas limit, allows to increase or decrease limit based on expected time vs real time for computing the chunk.
        pub gas_limit: Gas,
        /// Total balance burnt after processing the current chunk.
        pub balance_burnt: Balance,
        /// Congestion info about this shard after the chunk was applied.
        congestion_info: CongestionInfo,
        /// Requests for bandwidth to send receipts to other shards.
        pub bandwidth_requests: BandwidthRequests,
        /// Proposed split of this shard (dynamic resharding).
        pub proposed_split: Option<TrieSplit>,
    }
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L395-427)
```rust
// V4 -> V5: Add proposed split.
#[derive(BorshSerialize, BorshDeserialize, Clone, PartialEq, Eq, Debug, ProtocolSchema)]
pub struct ShardChunkHeaderInnerV5 {
    /// Previous block hash.
    pub prev_block_hash: CryptoHash,
    pub prev_state_root: StateRoot,
    /// Root of the outcomes from execution transactions and results of the previous chunk.
    pub prev_outcome_root: CryptoHash,
    pub encoded_merkle_root: CryptoHash,
    pub encoded_length: u64,
    pub height_created: BlockHeight,
    /// Shard index.
    pub shard_id: ShardId,
    /// Gas used in the previous chunk.
    pub prev_gas_used: Gas,
    /// Gas limit voted by validators.
    pub gas_limit: Gas,
    /// Total balance burnt in the previous chunk.
    pub prev_balance_burnt: Balance,
    /// Previous chunk's outgoing receipts merkle root.
    pub prev_outgoing_receipts_root: CryptoHash,
    /// Tx merkle root.
    pub tx_root: CryptoHash,
    /// Validator proposals from the previous chunk.
    pub prev_validator_proposals: Vec<ValidatorStake>,
    /// Congestion info about this shard after the previous chunk was applied.
    pub congestion_info: CongestionInfo,
    /// Requests for bandwidth to send receipts to other shards.
    pub bandwidth_requests: BandwidthRequests,
    /// Proposed split of this shard (dynamic resharding).
    /// `None` if the shard is not above the resharding threshold.
    pub proposed_split: Option<TrieSplit>,
}
```

**File:** docs/architecture/how/dynamic_resharding.md (L96-99)
```markdown
2. Calls `get_upcoming_shard_split()` which:
   - Checks if dynamic resharding is enabled (via `ShardLayoutConfig::Dynamic`).
   - Checks the resharding cooldown (`can_reshard()` -- verifies `epoch_height - last_resharding >= min_epochs_between_resharding`). `min_epochs_between_resharding` must be `> 0`: allowing back-to-back reshardings is unsafe because a freshly-created child shard would inherit `proposed_split` from the parent's final chunk while its own first chunk freshly computes `proposed_split = None`, triggering `InvalidChunkHeaderShardSplit`.
   - Calls `pick_shard_to_split()` to select the winning shard: forced shards have priority, otherwise the shard with highest `total_memory()` wins.
```

**File:** chain/chain/src/runtime/mod.rs (L591-605)
```rust
        if !ProtocolFeature::DynamicResharding.enabled(protocol_version) {
            return Ok(None);
        }

        let Some(config) = epoch_config.dynamic_resharding_config() else {
            return Ok(None);
        };

        if !self.epoch_manager.is_next_block_possibly_last_in_epoch(height, prev_block_hash)? {
            return Ok(None);
        }

        if !self.epoch_manager.can_reshard(prev_block_hash, config.min_epochs_between_resharding)? {
            return Ok(None);
        }
```

**File:** chain/epoch-manager/src/lib.rs (L2200-2210)
```rust
        let dynamic_resharding_config = match &epoch_config.shard_layout_config {
            ShardLayoutConfig::Static { .. } => return Ok(None),
            ShardLayoutConfig::Dynamic { dynamic_resharding_config } => dynamic_resharding_config,
        };

        // Check if resharding is allowed based on epoch constraints
        let can_reshard = self
            .can_reshard(&parent_hash, dynamic_resharding_config.min_epochs_between_resharding)?;
        if !can_reshard {
            return Ok(None);
        }
```
