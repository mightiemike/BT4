### Title
Unhandled `receiver_shard_id` error causes panic-triggered chain halt when processing delayed `GlobalContractDistribution` receipts across mixed resharding generations - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally `.unwrap()`s the `Result` returned by `Receipt::receiver_shard_id`. For `GlobalContractDistribution` receipts, that call can legitimately return `Err(EpochError::ShardingError(...))` when a receipt's `target_shard` cannot be resolved to any shard in the current layout's (V3) split-ancestor history. Any account can permissionlessly trigger this receipt type simply by deploying a global contract; if the receipt sits in the delayed-receipt queue across resharding events whose ancestor history does not fully cover it, every validator applying that chunk hits the same `unwrap()` and panics deterministically, halting chunk/block production.

### Finding Description
`Receipt::receiver_shard_id` (analog of GPAC's parser dereferencing an unpopulated field) explicitly documents that a `GlobalContractDistribution` receipt's `target_shard` "may be from an arbitrarily old layout" and, when `resolve_to_current_shard` cannot find a matching descendant, it returns an `Err` instead of a shard id: [1](#0-0) 

The only caller that filters delayed receipts by shard during chunk application ignores this `Result` and unwraps it unconditionally: [2](#0-1) 

This `receipt_filter_fn` is invoked from `pop()`, which is called every time the runtime drains the delayed-receipt queue during normal chunk application: [3](#0-2) [4](#0-3) 

The repository itself contains a regression test acknowledging the underlying hazard and stating that the mitigation (`resolve_to_current_shard`, backed by `ShardLayoutV3`'s cumulative `shards_ancestor_map`) is incomplete for mixed static/dynamic resharding histories: [5](#0-4) [6](#0-5) 

The test only exercises two *dynamic* (V3) resharding generations and explicitly notes: *"The fix only works with V3 shard layouts (dynamic resharding). With static resharding, the shard layout doesn't maintain a full split history."* Per the resharding design doc, a network transitioning from legacy static (V0/V1/V2) layouts to dynamic V3 layouts, or passing through a "Transitional" phase where a static layout change is carried forward without an ancestor map update, can produce a `ShardLayoutV3` whose `shards_ancestor_map` does not cover an old `target_shard` still referenced by a long-delayed `GlobalContractDistribution` receipt: [7](#0-6) [8](#0-7) 

When that happens, `receiver_shard_id` returns `Err`, and `receipt_filter_fn`'s `.unwrap()` panics inside `pop()`, which is called unconditionally by every node applying that chunk (`process_delayed_receipts` / `process_receipts`), producing an identical, deterministic panic across all honest validators applying the same state transition — a transaction-triggered halt.

### Impact Explanation
Because `pop()` is on the mandatory state-transition path for every chunk with a non-empty delayed-receipt queue, a panic here is not a single-node crash but a deterministic, network-wide halt: every honest validator applying the same chunk encounters the same unresolved `target_shard` and panics identically, since the runtime `apply` function is meant to be pure and deterministic across replicas. This satisfies the "transaction-triggered halt" acceptance bar — an ordinary account only needs to submit a `DeployGlobalContract` transaction (or otherwise cause creation of a `GlobalContractDistribution` receipt) and have that receipt delayed long enough across resharding-history-losing layout transitions.

### Likelihood Explanation
Exploitation requires the chain to undergo resharding transitions that lose ancestor coverage for an in-flight receipt (e.g. legacy-to-dynamic-resharding migration boundaries, or the "Transitional" carry-forward phase noted in the resharding design doc), combined with a delayed `GlobalContractDistribution` receipt surviving across that boundary. This is a narrower window than the two-dynamic-resharding case already fixed and tested, but it is an explicitly acknowledged, untested gap in the same code path, and the triggering transaction (deploying a global contract) requires no special privilege.

### Recommendation
Do not `.unwrap()` the `Result` from `Receipt::receiver_shard_id` inside `receipt_filter_fn`. Propagate the error out of `pop()` (making it return `Result<Option<..>, RuntimeError>` already does — the fix is only to stop swallowing the error inside the closure) so that an unresolvable `target_shard` becomes a handled `RuntimeError`/`StorageError` rather than a panic, and add ancestor-map coverage/migration logic so `ShardLayoutV3`'s `shards_ancestor_map` cannot lose reachability for shards referenced by receipts that predate a static→dynamic resharding transition.

### Proof of Concept
1. Deploy a global contract from any unprivileged account, producing a `GlobalContractDistribution` receipt targeting the deployer's shard.
2. Saturate the target shard's compute budget every block so the receipt is pushed into the delayed-receipt queue (mirrors `test_stale_global_contract_distribution_after_double_resharding`).
3. Drive the chain through a resharding sequence that includes a legacy static→dynamic migration boundary (or any transition where `shards_ancestor_map` does not retain the receipt's `target_shard` lineage), rather than two purely-dynamic splits as the existing regression test does.
4. When the delayed queue is drained, `receipt_filter_fn`'s `receiver_shard_id(...).unwrap()` panics on `Err(EpochError::ShardingError(...))`, halting chunk application deterministically on every validator applying that chunk.

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

**File:** runtime/runtime/src/congestion_control.rs (L868-878)
```rust
    // With ReshardingV3, it's possible for a chunk to have delayed receipts that technically
    // belong to the sibling shard before a resharding event.
    // Here, we filter all the receipts that don't belong to the current shard_id.
    //
    // The function follows the guidelines of standard iterator filter function
    // We return true if we should retain the receipt and false if we should filter it.
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-910)
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
    }
```

**File:** runtime/runtime/src/lib.rs (L2591-2606)
```rust
        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }

            let receipt = if let Some(receipt) = processing_state
                .delayed_receipts
                .pop(&mut processing_state.state_update, &processing_state.apply_state.config)?
            {
                receipt.into_receipt()
            } else {
                // Break loop if there are no more receipts to be processed.
                break;
            };
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-39)
```rust
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
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

**File:** docs/architecture/how/dynamic_resharding.md (L102-113)
```markdown
### 2.4 Deriving the New Shard Layout

During `EpochManager::finalize_epoch()`, when finalizing epoch N:

1. Read `block_info.shard_split()` from the last block of epoch N.
2. Call `next_next_shard_layout()` which has three phases:
   - **Static fallback**: If the next-next epoch config has a static layout, use it (backward compat).
   - **Transitional**: If the current epoch doesn't have dynamic resharding enabled, carry forward the existing layout.
   - **Dynamic**: If a split is present, call `ShardLayout::derive_v3()` to create a new `ShardLayoutV3`.
3. Store the resulting layout in `EpochInfoV5.shard_layout` for epoch N+2.

When bootstrapping from a legacy layout (V1 or V2), the system reconstructs the split history by calling `get_shard_layout_history()` to retrieve all historical layouts, then uses `ShardLayoutV3::derive_with_layout_history()` to build a V3 layout with full ancestor tracking.
```

**File:** core/primitives/src/shard_layout/v3.rs (L30-58)
```rust
fn validate_and_derive_shard_ancestor_map(
    shard_ids: &Vec<ShardId>,
    shards_split_map: &ShardsSplitMapV3,
) -> ShardsAncestorMapV3 {
    let mut shards_parent_map = BTreeMap::new();
    for (&parent_shard_id, child_shard_ids) in shards_split_map {
        assert!(
            !shard_ids.contains(&parent_shard_id),
            "shard that is split should no longer be used"
        );
        assert!(child_shard_ids.len() > 1, "shard must be split into at least two children");
        for &child_shard_id in child_shard_ids {
            let prev = shards_parent_map.insert(child_shard_id, parent_shard_id);
            assert!(prev.is_none(), "no shard should appear in the map twice");
        }
    }

    let mut shards_ancestor_map = ShardsAncestorMapV3::new();
    for shard_id in shard_ids {
        let mut ancestors = vec![];
        let mut current_id = *shard_id;
        while let Some(parent_shard_id) = shards_parent_map.get(&current_id) {
            ancestors.push(*parent_shard_id);
            current_id = *parent_shard_id;
        }
        shards_ancestor_map.insert(*shard_id, ancestors);
    }
    shards_ancestor_map
}
```
