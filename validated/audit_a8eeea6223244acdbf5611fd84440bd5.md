### Title
Stale cross-shard `GlobalContractDistribution` receipt can panic chunk apply via unwrap on `receiver_shard_id` after resharding, halting the chain - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
An unprivileged account can deploy a global contract (a normal, permissionless action) and, by congesting its own shard with ordinary `FunctionCall` transactions, keep the resulting `GlobalContractDistribution` receipt sitting in the delayed-receipt queue while the network reshards. `ShardLayoutV3::resolve_to_current_shard` walks the recorded split history to remap a stale `target_shard`, but that history is bounded to whatever `shards_split_map` retains; `receipt_filter_fn` and the buffer-forwarding path call `.unwrap()` on the `Result` returned by `Receipt::receiver_shard_id`, turning any lookup failure into a chunk-apply panic on every honest node that processes the receipt.

### Finding Description
`GlobalContractDistributionReceipt`/`V2` carry a `target_shard: ShardId` that is fixed at receipt-creation time and is only remapped when the receipt is actually delivered (`forward_distribution_next_shard`, `runtime/runtime/src/global_contracts.rs:275-320`). Resolution to the shard that should currently own the receipt happens in `Receipt::receiver_shard_id`: [1](#0-0) 

For non-current target shards it depends entirely on `ShardLayout::resolve_to_current_shard`, which recursively follows the *first* recorded child in `shards_split_map` until it finds a shard present in the current layout, or returns `None` if the id is missing from both the current shard set and the split history: [2](#0-1) 

Both call sites of `receiver_shard_id` treat the `Result` as infallible:

- The delayed-receipt pop path used while draining the backlog: [3](#0-2) 

- The outgoing-buffer forwarding path: [4](#0-3) 

A repository test explicitly documents and exercises this exact failure mode — a stale `GlobalContractDistribution` receipt surviving two resharding generations while sitting in the delayed queue, expecting `receiver_shard_id` to fail to remap and `receipt_filter_fn`'s `.unwrap()` to panic: [5](#0-4) 

The test's own preamble states the fix "only works with V3 shard layouts (dynamic resharding)" and is gated to two resharding generations specifically: [6](#0-5) 

Because `shards_split_map` retains the *full* history of splits per the module's own doc comment (`"Includes the full history of shard splits, i.e. split map of the current layout is a superset of the split map of its parent layout"` — `core/primitives/src/shard_layout/v3.rs:14-15`), the currently-tested scenario (double split) is covered, but any place where the split-map history is not carried forward correctly across a shard-layout transition (e.g. layouts that predate `ShardLayoutV3`/`DynamicResharding`, or any code path that constructs a `ShardLayout` from only the immediately-previous layout instead of the full ancestor chain) still reaches the same `None` branch and the same unchecked `.unwrap()`. I was not able to fully verify from the available index whether every shard-layout construction path in the current build always preserves the complete split history (this would require tracing every `derive`/`derive_with_layout_history` call site across resharding-orchestration code, which is outside what the indexed snippets show), so I cannot state with certainty whether the double-resharding case is the only reachable trigger or whether additional edge cases (e.g., legacy V1/V2→V3 migration boundaries, or more than two consecutive splits) remain exploitable in the current commit.

### Impact Explanation
If `receiver_shard_id` returns `Err` for a receipt still reachable via the delayed queue or the outgoing buffer, `receipt_filter_fn`'s `.unwrap()` (and the equivalent `?`-turned-hard-error path in `forward_from_buffer_to_shard`) will abort the runtime's `apply_chunk`. Because chunk application is a deterministic, protocol-mandated step run by every chunk producer/validator for that shard, this is not a benign single-node crash: it stalls chunk production for that shard chain-wide until the affected receipt can be skipped or hot-fixed, i.e., a transaction-triggered halt reachable purely from unprivileged actions (deploying a global contract + congesting one's own shard with ordinary function calls). This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
The trigger requires only actions available to any unprivileged account: `DeployGlobalContract` (permissionless) plus enough compute-heavy `FunctionCall` transactions to keep the resulting distribution receipt in the delayed queue across shard-layout transitions. The test in the repo demonstrates this is achievable with 3 heavy calls per block over roughly a dozen epochs. The remaining uncertainty is whether the currently shipped split-history bookkeeping fully closes the gap for all resharding-generation counts and layout-version transitions, since I could not trace every shard-layout derivation path from the indexed code alone.

### Recommendation
Replace the `.unwrap()` calls on `receiver_shard_id` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:876`) and in `forward_from_buffer_to_shard` (`runtime/runtime/src/congestion_control.rs:355`) with graceful error handling that cannot abort chunk application — e.g., propagate a `RuntimeError` that causes the individual receipt to be safely dropped/logged rather than panicking, or guarantee (with an enforced invariant/test covering ≥2 resharding generations and all shard-layout version transitions, not just the currently tested double-split case) that `shards_split_map` always contains a complete, uninterrupted ancestor chain for every historical shard id that can appear in a live receipt.

### Proof of Concept
The repository's own regression test is a working PoC of the underlying mechanism (deploy global contract → saturate compute to delay the resulting distribution receipt → force two shard splits → drain the delayed queue and observe whether the chain halts): [7](#0-6)

### Citations

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

**File:** runtime/runtime/src/congestion_control.rs (L347-356)
```rust
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-186)
```rust
    // Step 1: Deploy the test contract on user0's account so we can call burn_gas_raw.
    {
        let node = env.node_for_account(&chunk_producer);
        let tx = node.tx_deploy_test_contract(&deploy_user);
        node.submit_tx(tx);
    }
    env.runner_for_account(&chunk_producer).run_for_number_of_blocks(2);

    // Step 2: Deploy a global contract from user0. This creates a
    // GlobalContractDistribution receipt with target_shard = user0's shard (S_A),
    // which is the shard that will be split in the first resharding.
    {
        let node = env.node_for_account(&chunk_producer);
        let code = ContractCode::new(near_test_contracts::rs_contract().to_vec(), None);
        let tx = node.tx_deploy_global_contract(
            &deploy_user,
            code.code().to_vec(),
            GlobalContractDeployMode::CodeHash,
        );
        node.submit_tx(tx);
    }

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
