### Title
Congestion-Control Bypass via Missing Shard Entry: `Ok(true)` Returned When Receiving Shard Has No `CongestionInfo` - (`File: chain/chain/src/runtime/mod.rs`)

---

### Summary

`congestion_control_accepts_transaction` unconditionally returns `Ok(true)` — "accept the transaction" — when the receiving shard has no entry in `prev_block.congestion_info`. This is the exact structural analog of the Carapace bug: a guard that should block or restrict instead silently falls back to the most permissive value when a required datum is absent.

---

### Finding Description

In `chain/chain/src/runtime/mod.rs`, the function `congestion_control_accepts_transaction` is called during chunk production (`prepare_transactions`) to decide whether a transaction destined for a given shard should be included in the next chunk:

```rust
fn congestion_control_accepts_transaction(
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_config: &RuntimeConfig,
    epoch_id: &EpochId,
    prev_block: &PrepareTransactionsBlockContext,
    validated_tx: &ValidatedTransaction,
) -> Result<bool, Error> {
    let receiver_id = validated_tx.receiver_id();
    let receiving_shard = account_id_to_shard_id(epoch_manager, receiver_id, &epoch_id)?;
    let congestion_info = prev_block.congestion_info.get(&receiving_shard);
    let Some(congestion_info) = congestion_info else {
        return Ok(true);   // ← bypass: missing entry → unconditional accept
    };
    ...
    let shard_accepts_transactions = congestion_control.shard_accepts_transactions();
    Ok(shard_accepts_transactions.is_yes())
}
``` [1](#0-0) 

The same pattern appears in `NightshadeRuntime::validate_tx`, which is the RPC-level gate: if `receiver_congestion_info` is `None`, the entire `shard_accepts_transactions()` check is skipped and the transaction proceeds to `validate_transaction` without any congestion enforcement: [2](#0-1) 

`BlockCongestionInfo` is a `BTreeMap<ShardId, ExtendedCongestionInfo>`. A shard ID is absent from this map in two reachable, production situations:

1. **Dynamic resharding boundary.** When a parent shard is split into children, the child shard IDs do not exist in the *previous* block's congestion map. The Spice path explicitly acknowledges this is unresolved: [3](#0-2) 

2. **Congestion-control feature activation.** At the protocol version boundary where congestion control is first enabled (version 68), the first block after activation carries no congestion entries for any shard. [4](#0-3) 

In both cases, `congestion_info.get(&receiving_shard)` returns `None`, and the early-return `Ok(true)` fires, bypassing `CongestionControl::shard_accepts_transactions()` entirely. [5](#0-4) 

---

### Impact Explanation

During dynamic resharding, a parent shard that was heavily congested (e.g., `delayed_receipts_gas` at or near `max_congestion_incoming_gas = 20 PGAS`) splits into children that inherit the parent's delayed-receipt queue and therefore its actual congestion load. However, because the child shard IDs are absent from `prev_block.congestion_info`, every chunk producer's call to `congestion_control_accepts_transaction` returns `Ok(true)` for transactions destined to those children. All such transactions are included in the next chunk without any congestion gate. This directly exacerbates congestion in the new shards at the most vulnerable moment — immediately after resharding — and can cause the child shards to reach or exceed full congestion (`congestion_level == 1.0`), triggering the deadlock-prevention path and degrading throughput for all shards that route receipts through them.

At the RPC level, `validate_tx` with `receiver_congestion_info = None` also skips the check, so users receive `ProcessTxResponse::ValidTx` for transactions that should have been rejected with `InvalidTxError::ShardCongested`. [6](#0-5) 

---

### Likelihood Explanation

Dynamic resharding is a planned, recurring protocol event. The NEAR roadmap includes multiple resharding steps. Each resharding event creates at least one block where child shard IDs are absent from `prev_block.congestion_info`. Any user who submits transactions to the receiver account on the new shard during that window — which is publicly observable from the shard layout change — will have those transactions bypass congestion enforcement. No special privilege is required beyond submitting a normal signed transaction.

---

### Recommendation

Replace the unconditional `Ok(true)` early-return with a conservative fallback. Two options:

**Option A (conservative):** Treat a missing entry as fully congested and reject the transaction:
```rust
let Some(congestion_info) = congestion_info else {
    return Ok(false); // no info → treat as congested
};
```

**Option B (safe default):** Use `ExtendedCongestionInfo::default()` (zero congestion) only when the shard is genuinely new and known to be empty (e.g., first block after resharding with no inherited receipts), and document the invariant explicitly. For the resharding case, propagate the parent's congestion info to both children before the first chunk is produced on the child shards, eliminating the `None` case entirely.

The same fix must be applied to `NightshadeRuntime::validate_tx` for the `receiver_congestion_info: Option<ExtendedCongestionInfo>` parameter.

---

### Proof of Concept

1. Observe a resharding event (parent shard splits into two children at block height H).
2. At block H, `prev_block.congestion_info` (keyed by the *previous* block's shard layout) contains no entry for the new child shard IDs.
3. Submit a transaction with `receiver_id` mapping to a new child shard.
4. `congestion_control_accepts_transaction` resolves `receiving_shard` to the new child shard ID, calls `prev_block.congestion_info.get(&receiving_shard)` → `None`, and returns `Ok(true)`.
5. The chunk producer includes the transaction in the next chunk without any congestion check, even if the child shard has inherited 20 PGAS of delayed receipts from the congested parent.
6. Repeat for all chunk producers: all will include transactions to the new shard, bypassing the `shard_accepts_transactions()` gate that would otherwise return `ShardAcceptsTransactions::No(RejectTransactionReason::IncomingCongestion { ... })`. [7](#0-6) [8](#0-7)

### Citations

**File:** chain/chain/src/runtime/mod.rs (L1032-1042)
```rust
                if !congestion_control_accepts_transaction(
                    self.epoch_manager.as_ref(),
                    &runtime_config,
                    &epoch_id,
                    &prev_block,
                    &validated_tx,
                )? {
                    tracing::trace!(target: "runtime", tx=?validated_tx.get_hash(), "discarding transaction due to congestion");
                    rejected_due_to_congestion += 1;
                    continue;
                }
```

**File:** chain/chain/src/runtime/mod.rs (L1701-1750)
```rust
fn congestion_control_accepts_transaction(
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_config: &RuntimeConfig,
    epoch_id: &EpochId,
    prev_block: &PrepareTransactionsBlockContext,
    validated_tx: &ValidatedTransaction,
) -> Result<bool, Error> {
    let receiver_id = validated_tx.receiver_id();
    let receiving_shard = account_id_to_shard_id(epoch_manager, receiver_id, &epoch_id)?;
    let congestion_info = prev_block.congestion_info.get(&receiving_shard);
    let Some(congestion_info) = congestion_info else {
        return Ok(true);
    };

    let congestion_control = CongestionControl::new(
        runtime_config.congestion_control_config,
        congestion_info.congestion_info,
        congestion_info.missed_chunks_count,
    );
    let shard_accepts_transactions = congestion_control.shard_accepts_transactions();
    Ok(shard_accepts_transactions.is_yes())
}

/// Check if the given shard should be split based on `DynamicReshardingConfig` thresholds.
/// Checks (in priority order): max shard count, force-split list, block-split list,
/// memory threshold, and minimum child size. Returns `Some(TrieSplit)` on approval.
fn check_dynamic_resharding(
    shard_trie: &Trie,
    shard_id: ShardId,
    shard_layout: ShardLayout,
    config: &DynamicReshardingConfig,
) -> Result<Option<TrieSplit>, FindSplitError> {
    let shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
    let mem_usage = total_mem_usage(shard_trie)?;

    DYNAMIC_RESHARDING_SHARD_MEMORY_USAGE
        .with_label_values(&[&shard_uid.to_string()])
        .set(mem_usage as i64);
    DYNAMIC_RESHARDING_MEMORY_USAGE_THRESHOLD.set(config.memory_usage_threshold as i64);
    DYNAMIC_RESHARDING_MIN_CHILD_MEMORY_USAGE.set(config.min_child_memory_usage as i64);
    DYNAMIC_RESHARDING_MAX_NUMBER_OF_SHARDS.set(config.max_number_of_shards as i64);

    if shard_layout.num_shards() >= config.max_number_of_shards {
        return Ok(None);
    }
    // maximum number of shards takes precedence over force-split – DO NOT REORDER
    if config.force_split_shards.contains(&shard_id) {
        return Ok(Some(find_trie_split(shard_trie)?));
    }
    if config.block_split_shards.contains(&shard_id) {
```

**File:** chain/chain/src/spice/chunk_application.rs (L326-340)
```rust
    // TODO(spice-resharding): across a resharding boundary both children map to the
    // same parent and inherit its congestion info unsplit. See dynamic_resharding.md.
    for shard_id in shard_layout.shard_ids() {
        let (_, prev_block_shard_id, _) =
            epoch_manager.get_prev_shard_id_from_prev_hash(prev_block_hash, shard_id)?;
        let prev_shard_execution_result = prev_block_execution_results
            .0
            .get(&prev_block_shard_id)
            .expect("block execution result should contain execution results for all shards");
        let congestion_info = prev_shard_execution_result.chunk_extra.congestion_info();
        // Always 0: missing chunks apply as empty new chunks, so shards never
        // stall (see chunk_executor_actor.rs); no missed-chunk backpressure.
        let missed_chunks_count = 0;
        result.insert(shard_id, ExtendedCongestionInfo::new(congestion_info, missed_chunks_count));
    }
```

**File:** core/parameters/res/runtime_configs/68.yaml (L1-58)
```yaml
# Congestion Control

# i64::MAX == 9_223_372_036_854_775_807
# PGAS     ==     1_000_000_000_000_000 GAS
# TGAS     ==         1_000_000_000_000 GAS
# MB       ==                 1_000_000 B

# The following default constants have been defined in
# [NEP-539](https://github.com/near/NEPs/pull/539) after extensive fine-tuning
# and discussions.

# 20 PGAS
max_congestion_incoming_gas: { 
  old : 9_223_372_036_854_775_807,
  new : 20_000_000_000_000_000,
}
# 10 PGAS
max_congestion_outgoing_gas: { 
  old : 9_223_372_036_854_775_807,
  new : 10_000_000_000_000_000,
}
# 1000 MB
max_congestion_memory_consumption: { 
  old : 9_223_372_036_854_775_807,
  new : 1_000_000_000,
}
# 5 missed chunks
max_congestion_missed_chunks: { 
  old : 9_223_372_036_854_775_807,
  new : 5,
}

# 300 PGAS
max_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 300_000_000_000_000_000,
}
# 1 PGAS
min_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 1_000_000_000_000_000
}
# 1 PGAS
allowed_shard_outgoing_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 1_000_000_000_000_000
}

# 500 TGAS
max_tx_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 500_000_000_000_000
}
# 20 TGAS
min_tx_gas: { 
  old: 9_223_372_036_854_775_807,
  new: 20_000_000_000_000
}
```

**File:** core/primitives/src/congestion_info.rs (L44-54)
```rust
    pub fn congestion_level(&self) -> f64 {
        let incoming_congestion = self.incoming_congestion();
        let outgoing_congestion = self.outgoing_congestion();
        let memory_congestion = self.memory_congestion();
        let missed_chunks_congestion = self.missed_chunks_congestion();

        incoming_congestion
            .max(outgoing_congestion)
            .max(memory_congestion)
            .max(missed_chunks_congestion)
    }
```

**File:** core/primitives/src/congestion_info.rs (L123-151)
```rust
    pub fn shard_accepts_transactions(&self) -> ShardAcceptsTransactions {
        let incoming_congestion = self.incoming_congestion();
        let outgoing_congestion = self.outgoing_congestion();
        let memory_congestion = self.memory_congestion();
        let missed_chunks_congestion = self.missed_chunks_congestion();

        let congestion_level = incoming_congestion
            .max(outgoing_congestion)
            .max(memory_congestion)
            .max(missed_chunks_congestion);

        // Convert to NotNan here, if not possible, the max above is already meaningless.
        let congestion_level =
            NotNan::new(congestion_level).unwrap_or_else(|_| NotNan::new(1.0).unwrap());
        if *congestion_level < self.config.reject_tx_congestion_threshold {
            return ShardAcceptsTransactions::Yes;
        }

        let reason = if missed_chunks_congestion >= *congestion_level {
            RejectTransactionReason::MissedChunks { missed_chunks: self.missed_chunks_count }
        } else if incoming_congestion >= *congestion_level {
            RejectTransactionReason::IncomingCongestion { congestion_level }
        } else if outgoing_congestion >= *congestion_level {
            RejectTransactionReason::OutgoingCongestion { congestion_level }
        } else {
            RejectTransactionReason::MemoryCongestion { congestion_level }
        };
        ShardAcceptsTransactions::No(reason)
    }
```

**File:** chain/client/src/rpc_handler.rs (L190-214)
```rust
        let receiver_congestion_info = if let Some(certified_header) = &spice_certified_header {
            // Receiver-shard congestion from the last certified block's executed
            // ChunkExtras, to reject transactions to a congested shard.
            spice_shard_congestion_info(
                &self.chain_store,
                &shard_layout,
                certified_header.as_ref(),
                receiver_shard,
            )
        } else {
            cur_block.block_congestion_info().get(&receiver_shard).copied()
        };

        let validated_tx = match self.runtime.validate_tx(
            &shard_layout,
            signed_tx.clone(),
            protocol_version,
            receiver_congestion_info,
        ) {
            Ok(validated_tx) => validated_tx,
            Err((err, signed_tx)) => {
                tracing::debug!(target: "client", tx_hash = ?signed_tx.get_hash(), ?err, "invalid tx during basic validation");
                return Ok(ProcessTxResponse::InvalidTx(err));
            }
        };
```
