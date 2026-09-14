## Analog Found

### Title
Unhandled panic in `receipt_filter_fn` when a `GlobalContractDistributionReceipt`'s target shard cannot be resolved across resharding generations — ([File: runtime/runtime/src/congestion_control.rs])

### Summary
The Assimp CVE is a null/invalid pointer dereference reached when `SplitLargeMeshesProcess_Triangle::UpdateNode` assumes a parent-node pointer is always valid after a mesh split. The nearcore analog is an `.unwrap()` on a `Result` that assumes a receipt's shard-remapping lookup always succeeds after a chunk/shard "split" (resharding), even though the underlying resolver can legitimately return `None`/`Err` for older shard-layout versions.

### Finding Description
`receipt_filter_fn` blindly unwraps the result of `receiver_shard_id`: [1](#0-0) 

`Receipt::receiver_shard_id` returns `Err(EpochError::ShardingError(...))` for a `GlobalContractDistribution` receipt whenever its `target_shard` is not in the current shard layout **and** cannot be resolved via `ShardLayout::resolve_to_current_shard`: [2](#0-1) 

`resolve_to_current_shard` is only fully correct (multi-generation walk) for `ShardLayoutV3`. For `V0`/`V1`/`V2` layouts it performs a **single-generation** lookup only: [3](#0-2) 

whereas the V3 implementation recursively walks the full cumulative `shards_split_map`: [4](#0-3) 

Static resharding (V1/V2, driven by protocol-version upgrades) only stores the most recent split map, per the resharding docs: "V2 stores only the most recent split map" vs. V3's "full cumulative split history" [5](#0-4) . Consequently, if a `GlobalContractDistributionReceipt` sits in the delayed-receipt queue (or is otherwise still in flight) across **two or more** resharding transitions while the layout is V1/V2, `get_children_shards_ids` can no longer map the receipt's stale `target_shard` to any shard in the current layout, `resolve_to_current_shard` returns `None`, `receiver_shard_id` returns `Err`, and `receipt_filter_fn`'s `.unwrap()` panics.

This is exactly the bug class the codebase's own regression test targets for the V3 case (`test_stale_global_contract_distribution_after_double_resharding`), which explicitly documents: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations" [6](#0-5) . The fix (recursive `resolve_to_current_shard`) closes the gap for V3 but the same latent panic path remains reachable through the V1/V2 single-generation code path, and `receipt_filter_fn` itself still has no error handling — any future or legacy path that returns `Err` from `receiver_shard_id` crashes the node.

`receipt_filter_fn` is invoked from `DelayedReceiptQueueWrapper::pop`, which runs during ordinary chunk application (`pop` is called while draining delayed receipts) [7](#0-6) , i.e., on the hot path every validator/chunk-producer executes when applying a chunk.

### Impact Explanation
A panic inside chunk application is not a per-transaction failure — it aborts the whole `apply` call for that shard on every node that must process that chunk, i.e., a transaction/receipt-triggered halt of chunk production/validation for all honest nodes tracking that shard (denial of service, consensus-visible stall) — matching the CVSS 5.5/AV:L/AC:L/PR:L "denial of availability" profile of the original CVE, scaled up because here it can be triggered by an ordinary account deploying a global contract (a permissionless action available to any signer) and letting delayed-queue backpressure keep it in flight across two resharding events.

### Likelihood Explanation
Reaching this requires: (1) a global contract deployment tx from any account (fully permissionless), (2) the receipt getting delayed long enough (achievable via compute/gas saturation, as already demonstrated by the existing test harness) to survive two resharding transitions, and (3) the shard layout in that window being V1/V2 (static resharding) rather than V3. Static resharding is still a supported/used mechanism for protocol-version-driven shard changes, so the precondition is plausible, though it requires alignment with a legacy (non-dynamic-resharding) network configuration undergoing back-to-back layout changes, making this moderate-likelihood rather than trivially always-reachable.

### Recommendation
- Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (return a `Result`/`RuntimeError` instead of panicking), so an unresolved shard falls back to a safe default (e.g., treat as belonging to current shard, or explicitly drop/log) rather than crashing the node.
- Extend `resolve_to_current_shard` for `V0`/`V1`/`V2` to walk multiple generations (mirroring the V3 implementation) instead of doing a single-hop lookup, or reject/convert legacy layouts to V3 semantics before this resolution is attempted.
- Add a regression test analogous to `test_stale_global_contract_distribution_after_double_resharding` but exercised against a V1/V2 (static resharding) shard-layout history.

### Proof of Concept
1. Configure a network using static (V1/V2) shard layouts across at least two protocol-version-driven resharding events.
2. From any account, submit a `DeployGlobalContract` transaction targeting a shard that will be split in the next resharding.
3. Saturate compute on that shard (e.g., repeated high-gas `FunctionCall`s) each block so the resulting `GlobalContractDistributionReceipt` is pushed into the delayed-receipt queue and stays there through two consecutive resharding transitions (mirrors the existing test's saturation loop) [8](#0-7) .
4. Stop saturating and let the delayed queue drain; when the runtime pops the stale receipt, `receiver_shard_id` returns `Err` because `resolve_to_current_shard` (V1/V2 single-generation path) cannot map the twice-stale `target_shard`, and `receipt_filter_fn`'s `.unwrap()` panics, aborting chunk application on every node tracking that shard.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-909)
```rust
    pub(crate) fn pop(
        &mut self,
        trie_update: &mut TrieUpdate,
        config: &RuntimeConfig,
    ) -> Result<Option<ReceiptOrStateStoredReceipt<'_>>, RuntimeError> {
        // While processing receipts, we need to keep track of the gas and bytes
        // even for receipts that may be filtered out due to a resharding event
        loop {
            // Check proof size limit before each receipt is popped.
            if trie_update.trie.check_proof_size_limit_exceed() {
                break;
            }
            let Some(receipt) = self.queue.pop_front(trie_update)? else {
                break;
            };
            let delayed_gas = receipt_congestion_gas(&receipt, &config)?;
            let delayed_bytes = receipt_size(&receipt)? as u64;
            self.removed_delayed_gas =
                self.removed_delayed_gas.checked_add(delayed_gas).ok_or(IntegerOverflowError)?;
            self.removed_delayed_bytes = self
                .removed_delayed_bytes
                .checked_add(delayed_bytes)
                .ok_or(IntegerOverflowError)?;

            // Track gas and bytes for receipt above and return only receipt that belong to the shard.
            if self.receipt_filter_fn(&receipt) {
                return Ok(Some(receipt));
            }
        }
        Ok(None)
```

**File:** core/primitives/src/receipt.rs (L447-463)
```rust
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
```

**File:** core/primitives/src/shard_layout/mod.rs (L227-237)
```rust
    /// Resolve any historical shard ID to a current descendant shard ID.
    /// For V3 layouts this uses the complete `shards_split_map`.
    /// For older layouts it falls back to `get_children_shards_ids` (single generation only).
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        match self {
            Self::V0(_) | Self::V1(_) | Self::V2(_) => {
                self.get_children_shards_ids(shard_id).map(|c| c[0])
            }
            Self::V3(v3) => v3.resolve_to_current_shard(shard_id),
        }
    }
```

**File:** core/primitives/src/shard_layout/v3.rs (L315-326)
```rust
    /// Resolve any historical shard ID to a current shard by walking the full
    /// split history in `shards_split_map`. Returns the shard itself if it is
    /// current, or follows the first child at each generation until a current
    /// shard is reached. Returns `None` only if the shard ID is absent from
    /// both the current layout and the split history.
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        if self.shard_ids.contains(&shard_id) {
            return Some(shard_id);
        }
        let children = self.shards_split_map.get(&shard_id)?;
        self.resolve_to_current_shard(children[0])
    }
```

**File:** docs/architecture/how/dynamic_resharding.md (L203-210)
```markdown
| Aspect | Static Resharding | Dynamic Resharding |
|--------|------------------|--------------------|
| **Shard layout source** | `EpochConfig.shard_layout` (determined by protocol version) | `EpochInfo.shard_layout` (stored per-epoch in V5) |
| **When layout changes** | Only on protocol version upgrade | Automatically at epoch boundaries based on trie memory usage |
| **Layout version** | V0, V1, or V2 | V3 (with cumulative split history) |
| **Config type** | `ShardLayoutConfig::Static { shard_layout }` | `ShardLayoutConfig::Dynamic { dynamic_resharding_config }` |
| **Split history** | V2 stores only the most recent split map | V3 stores full cumulative split history + ancestor maps |
| **EpochInfo version** | V4 or earlier | V5 (adds `shard_layout` and `last_resharding` fields) |
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-162)
```rust
    // Step 3: Saturate compute on user0's shard every block so that the
    // GlobalContractDistribution receipt (arriving as incoming) gets pushed to
    // the delayed queue and stays there through both resharding events.
    //
    // Each burn_gas_raw call burns slightly more than half the gas limit, so
    // two local receipts exhaust the chunk's compute budget. We submit 3 per
    // block to ensure at least 2 are processed as local receipts.
    let gas_to_burn = gas_limit.checked_div(2).unwrap().checked_add(Gas::from_gas(1)).unwrap();
    let initial_num_shards = base_shard_layout.num_shards();
    let target_num_shards = initial_num_shards + 2; // after two splits

    let start_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };

    // Keep saturating until both resharding events complete. Dynamic resharding has a
    // 2-epoch proposal-to-activation pipeline, so we need enough epochs for both splits.
    let max_saturation_height = start_height + epoch_length * 12;
    let mut both_splits_done = false;
    for target_height in (start_height + 1)..=max_saturation_height {
        // Submit 3 heavy transactions to saturate this block's compute budget.
        {
            let node = env.node_for_account(&chunk_producer);
            for _ in 0..3 {
                let tx = node.tx_call(
                    &deploy_user,
                    &deploy_user,
                    "burn_gas_raw",
                    gas_to_burn.as_gas().to_le_bytes().to_vec(),
                    Balance::ZERO,
                    gas_limit,
                );
                node.submit_tx(tx);
            }
        }
        env.runner_for_account(&chunk_producer).run_until_head_height(target_height);

        // Check if both resharding events have completed.
        let node = env.node_for_account(&chunk_producer);
        let epoch_id = node.client().chain.chain_store().head().unwrap().epoch_id;
        let current_layout = node.client().epoch_manager.get_shard_layout(&epoch_id).unwrap();
        if current_layout.num_shards() >= target_num_shards {
            both_splits_done = true;
            break;
        }
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-185)
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
```
