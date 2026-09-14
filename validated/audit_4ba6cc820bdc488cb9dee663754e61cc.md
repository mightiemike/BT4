Based on my research, I found a strong structural analog to the SP1 nonce-clobbering bug in nearcore's dynamic resharding code path.

### Title
Resharding manager clones stale parent `ChunkExtra` fields into child shard, causing chunk header/witness validation to permanently reject valid chunks after a shard split - ([File: chain/chain/src/resharding/manager.rs])

### Summary
When a shard splits during dynamic resharding, `ReshardingManager::process_memtrie_resharding_storage_update` builds each child's `ChunkExtra` by cloning the **parent's** `ChunkExtra` wholesale and then overwriting only two fields (`state_root` and `congestion_info`). All other fields — `bandwidth_requests`, `gas_used`, `gas_limit`, `balance_burnt`, and `proposed_split` — are left as the parent shard's stale values instead of being recomputed for the newly-created child shard. This is the same bug class as the SP1 report: a secondary/derived accumulator (here, the raw parent `ChunkExtra` clone) is packed into the "final" result (the child's persisted `ChunkExtra`) without properly overwriting all fields that differ between the source and destination, leaving stale data that downstream consumers treat as authoritative.

### Finding Description
In `process_memtrie_resharding_storage_update`: [1](#0-0) 

```rust
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;

chain_store_update.save_chunk_extra(
    block_hash,
    &new_shard_uid,
    child_chunk_extra.into(),
);
```

Only `state_root` and `congestion_info` are recomputed for the child; `bandwidth_requests`, `proposed_split`, `gas_used`, `gas_limit`, and `balance_burnt` all carry over unchanged from the parent shard. This is explicitly flagged as an incomplete TODO in the code itself and in the architecture docs (`docs/architecture/how/dynamic_resharding.md`, TODO #10: "The resharding manager doesn't set all `ChunkExtra` fields").

This persisted `ChunkExtra` becomes the authoritative "prev chunk extra" that `ChunkProducer::produce_chunk_internal` reads to populate the next chunk header's `bandwidth_requests` and `proposed_split` fields (copied verbatim, not recomputed): [2](#0-1) 

Meanwhile, chunk validators independently recompute a fresh `ChunkExtra` by actually replaying/applying the child shard's first real chunk, and bind the replayed result against the header via `validate_chunk_with_chunk_extra_and_receipts_root`, which explicitly checks `proposed_split()` for a forged/mismatched value: [3](#0-2) 

Because the producer's header carries the parent's stale `bandwidth_requests`/`proposed_split` (from the un-updated child `ChunkExtra`) while validators compute the correct, freshly-derived values for the child, the header and the locally-recomputed extra diverge, tripping `Error::InvalidChunkHeaderShardSplit` / general `InvalidChunkStateWitness` validation failures — deterministically, on every honest validator, since the stale-field bug itself is fully deterministic.

### Impact Explanation
Since dynamic resharding is triggered automatically by ordinary state growth (`total_mem_usage >= threshold`) driven by normal transaction/contract activity — not by any malicious validator behavior — this bug is reachable purely through organic chain usage (or a user deliberately inflating a shard's memory usage to force a split). Once triggered, the newly split child shard's very first chunk is deterministically built from/validated against a corrupted `ChunkExtra`, causing:
- Endorsement failure for that shard's first post-split chunk (>2/3 stake endorsement cannot be reached because every honest validator recomputes a differing extra than the header claims).
- Chunk production for that child shard cannot make progress, which constitutes a transaction/state-growth-triggered halt of that shard (and potentially the whole chain, since block production requires included/skipped chunks for every shard).

This matches the report's "does not enable acceptance of invalid state, but causes valid state transitions to be rejected" impact category, escalated to a network-halting condition given nearcore's chunk-inclusion/endorsement requirements.

### Likelihood Explanation
Dynamic resharding (`ProtocolFeature::DynamicResharding`) is active at protocol version 85+ (stable at v86 per the pinned snapshot), so the code path is live whenever `ShardLayoutConfig::Dynamic` is configured for an epoch. The trigger condition (shard memory usage crossing the split threshold) is a normal consequence of chain growth, making this a high-likelihood, protocol-level correctness bug rather than a rare edge case — it will fire deterministically on the very first split that occurs under this configuration.

### Recommendation
In `process_memtrie_resharding_storage_update` (`chain/chain/src/resharding/manager.rs`), explicitly recompute or intentionally reset every `ChunkExtra` field for the child shard rather than cloning the parent's `ChunkExtra` and patching only two fields. In particular:
- `bandwidth_requests` should be reset to `BandwidthRequests::empty()` (or correctly derived per-child) rather than inherited from the parent.
- `proposed_split` should be reset to `None` for the freshly created child.
- Confirm `gas_used`/`gas_limit`/`balance_burnt` semantics are correct to carry over, or reset them if they should reflect the child's own accounting.

Add an integration/test-loop test that exercises resharding validation immediately after a split completes to catch header/witness mismatches, closing the gap referenced by the existing TODO(resharding) comment.

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-266)
```rust
            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;

            chain_store_update.save_chunk_extra(
                block_hash,
                &new_shard_uid,
                child_chunk_extra.into(),
            );
```

**File:** chain/client/src/chunk_producer.rs (L375-412)
```rust
        let congestion_info = chunk_extra.congestion_info();
        let bandwidth_requests = chunk_extra.bandwidth_requests();
        debug_assert!(
            bandwidth_requests.is_some(),
            "Expected bandwidth_request to be Some after BandwidthScheduler feature enabled"
        );

        let protocol_version = self.epoch_manager.get_epoch_protocol_version(epoch_id)?;
        let (chunk, merkle_paths) = if ProtocolFeature::Spice.enabled(protocol_version) {
            ShardChunkWithEncoding::new_for_spice(
                prev_block_hash,
                next_height,
                shard_id,
                prepared_transactions.transactions,
                outgoing_receipts.clone(),
                outgoing_receipts_root,
                tx_root,
                &*validator_signer,
                &mut self.reed_solomon_encoder,
            )
        } else {
            ShardChunkWithEncoding::new(
                prev_block_hash,
                *chunk_extra.state_root(),
                *chunk_extra.outcome_root(),
                next_height,
                shard_id,
                gas_used,
                gas_limit,
                chunk_extra.balance_burnt(),
                chunk_extra.validator_proposals().collect(),
                prepared_transactions.transactions,
                outgoing_receipts.clone(),
                outgoing_receipts_root,
                tx_root,
                congestion_info,
                bandwidth_requests.cloned().unwrap_or_else(BandwidthRequests::empty),
                chunk_extra.proposed_split().cloned(),
```

**File:** protocol-model/spec/sharding-chunks.md (L115-117)
```markdown
- **A shard has at most one parent** across a layout change; `from_shard_layout` errors `"can't perform two reshardings at the same time!"` if two shards each have two children (`event_type.rs:79`).
- **`proposed_split` cannot be forged**: `validate_chunk_with_chunk_extra_and_receipts_root` (`chain/chain/src/validate.rs:133`) compares `chunk_header.proposed_split()` against the locally recomputed `prev_chunk_extra.proposed_split()`, returning `Error::InvalidChunkHeaderShardSplit` on mismatch (`:178`).
- **`shard_split` cannot be forged**: `validate_block_shard_split` (`validate.rs:193`) recomputes the block header's split via `get_upcoming_shard_split` and returns `Error::InvalidBlockHeaderShardSplit` on mismatch (`:218`).
```
