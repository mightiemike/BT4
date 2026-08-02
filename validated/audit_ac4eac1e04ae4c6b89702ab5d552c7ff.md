## Finding: Confirmed structural bug, but scope-limited

I traced the code path and found a genuine bug, though its applicability to mainnet consensus needs a caveat since I couldn't fully confirm within this session's budget whether `ShardedBlockExecutor` participates in mainnet consensus-critical block commit versus being used only for remote/experimental execution.

### Title
`get_usage()` always returns an untracked placeholder inside sharded execution, corrupting `StateStorageUsage` for every sharded block - (File: `aptos-move/aptos-vm/src/sharded_block_executor/cross_shard_state_view.rs`)

### Summary
Inside the sharded block executor, `CrossShardStateView::get_usage` unconditionally returns `StateStorageUsage::new_untracked()` instead of delegating to the real global base view, and `AggregatorOverriddenStateView::get_usage` (the outer view actually handed to the VM) simply forwards to this inner view without correcting it.

### Finding Description
`AggregatorOverriddenStateView::get_usage` delegates straight to its `base_view`: [1](#0-0) 

In the sharded executor, `base_view` for `AggregatorOverriddenStateView` is always a `CrossShardStateView`, constructed in both regular-shard and "global shard" (round with `shard_id: None`) execution: [2](#0-1) 

But `CrossShardStateView::get_usage` does **not** forward to its own `base_view` (which does hold the real, full ledger state) — it hard-codes an untracked placeholder: [3](#0-2) 

Compare this with `get_state_value`, which correctly falls back to `self.base_view.get_state_value(state_key)` when the key is not in the cross-shard cache set: [4](#0-3) 

No such fallback exists for `get_usage`. This means that whenever the Move VM executes `state_storage::get_state_storage_usage_only_at_epoch_beginning` (invoked from `state_storage::on_new_block` at the first transaction of each new epoch) under the sharded block executor, the underlying `NativeStateStorageContext` resolves `get_usage()` through this chain and receives `StateStorageUsage::new_untracked()` rather than the correct full-ledger usage: [5](#0-4) 

This corrupts the value written into the on-chain `StateStorageUsage.usage` resource, which is documented as needing to "reflect the storage usage after the last txn of the previous epoch is committed" for the *entire* ledger: [6](#0-5) 

### Impact Explanation
`StateStorageUsage` feeds `storage_gas` calculations (`current_items_and_bytes`) that determine per-transaction storage gas pricing for the entire epoch: [7](#0-6) 

If a block containing the epoch-boundary transaction is executed via the sharded path, the resulting `StateStorageUsage` resource (and hence storage gas schedule for the whole next epoch) would differ from what a non-sharded execution of the identical block would produce. That is a state-commitment divergence: two conformant executors (sharded vs. non-sharded) computing different post-state for the same input block, which is a hard-fork class bug if both execution modes are used to independently produce/verify the same committed ledger state.

### Likelihood Explanation
This is not input-dependent — it fires deterministically on every block that triggers `on_new_block`'s epoch-changed branch while using the sharded executor, since **every** round (including the "global" round used for `shard_id: None`) is wrapped in `CrossShardStateView` before `AggregatorOverriddenStateView` is applied. I was not able to confirm within the available tool budget whether the `ShardedBlockExecutor` path is actually exercised for consensus-critical mainnet block execution today (as opposed to only remote-executor benchmarking/experimental use), nor did I confirm the exact `items()`/`bytes()` semantics of `StateStorageUsage::Untracked` in `types/src/state_store/state_storage_usage.rs` (I located but did not fully read that file). Both points materially affect whether this is exploitable on mainnet versus being latent/experimental-only code, and should be verified before treating this as confirmed mainnet impact.

### Recommendation
Make `CrossShardStateView::get_usage` delegate to `self.base_view.get_usage()` (mirroring the fallback pattern already used in `get_state_value`), so the aggregated/global usage is preserved through the sharded execution wrapping chain, and add a differential test asserting bit-for-bit equality of the committed `StateStorageUsage` resource between sharded and non-sharded execution of the same block (as the exploit question proposes).

### Proof of Concept
Not independently constructed/run in this review; the proof-of-concept described in the question (execute an identical block containing an epoch-boundary transaction through both `AptosVMBlockExecutor::execute_block` and `execute_block_sharded`, then compare the resulting `state_storage::StateStorageUsage` resource) is the correct way to demonstrate the divergence, given `CrossShardStateView::get_usage`'s hard-coded `new_untracked()` return shown above.

### Citations

**File:** aptos-move/aptos-vm/src/sharded_block_executor/aggr_overridden_state_view.rs (L52-54)
```rust
    fn get_usage(&self) -> Result<StateStorageUsage> {
        self.base_view.get_usage()
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_executor_service.rs (L115-126)
```rust
        let cross_shard_state_view = Arc::new(CrossShardStateView::create_cross_shard_state_view(
            state_view,
            &transactions,
        ));

        let cross_shard_state_view_clone = cross_shard_state_view.clone();
        let cross_shard_client_clone = cross_shard_client.clone();

        let aggr_overridden_state_view = Arc::new(AggregatorOverriddenStateView::new(
            cross_shard_state_view.as_ref(),
            TOTAL_SUPPLY_AGGR_BASE_VAL,
        ));
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/cross_shard_state_view.rs (L77-82)
```rust
    fn get_state_value(&self, state_key: &StateKey) -> Result<Option<StateValue>, StateViewError> {
        if let Some(value) = self.cross_shard_data.get(state_key) {
            return Ok(value.get_value());
        }
        self.base_view.get_state_value(state_key)
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/cross_shard_state_view.rs (L84-86)
```rust
    fn get_usage(&self) -> Result<StateStorageUsage, StateViewError> {
        Ok(StateStorageUsage::new_untracked())
    }
```

**File:** aptos-move/framework/aptos-framework/sources/state_storage.move (L17-22)
```text
    /// This is updated at the beginning of each epoch, reflecting the storage
    /// usage after the last txn of the previous epoch is committed.
    struct StateStorageUsage has key, store {
        epoch: u64,
        usage: Usage,
    }
```

**File:** aptos-move/framework/aptos-framework/sources/state_storage.move (L39-49)
```text
    public(friend) fun on_new_block(epoch: u64) acquires StateStorageUsage {
        assert!(
            exists<StateStorageUsage>(@aptos_framework),
            error::not_found(ESTATE_STORAGE_USAGE)
        );
        let usage = borrow_global_mut<StateStorageUsage>(@aptos_framework);
        if (epoch != usage.epoch) {
            usage.epoch = epoch;
            usage.usage = get_state_storage_usage_only_at_epoch_beginning();
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/state_storage.move (L51-58)
```text
    public(friend) fun current_items_and_bytes(): (u64, u64) acquires StateStorageUsage {
        assert!(
            exists<StateStorageUsage>(@aptos_framework),
            error::not_found(ESTATE_STORAGE_USAGE)
        );
        let usage = borrow_global<StateStorageUsage>(@aptos_framework);
        (usage.usage.items, usage.usage.bytes)
    }
```
