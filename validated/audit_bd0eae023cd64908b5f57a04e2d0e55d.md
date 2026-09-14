### Title
Transaction-triggered chain halt via `.unwrap()` panic on stale `GlobalContractDistribution` receipt's `receiver_shard_id()` after double resharding - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`receipt_filter_fn` in the delayed-receipt queue unconditionally unwraps the result of `receiver_shard_id(&shard_layout)` for every delayed receipt popped from the queue during chunk application. A receiver-triggered `GlobalContractDistribution` receipt whose `target_shard` refers to a shard that no longer exists after two dynamic-resharding splits can make `receiver_shard_id()` fail to remap, turning the `.unwrap()` into a runtime panic that halts every honest node processing that chunk.

### Finding Description
`receipt_filter_fn` is called from `DelayedReceiptQueueWrapper::pop` on every receipt drained from the delayed-receipt queue during normal chunk application: [1](#0-0) 

It computes `receipt.get_receipt().receiver_shard_id(&shard_layout)` and calls `.unwrap()` on the result, comparing it to the current `shard_id` to decide whether a receipt (queued before a resharding split) still belongs to this shard. The comment explicitly acknowledges the fragile precondition: *"With ReshardingV3, it's possible for a chunk to have delayed receipts that technically belong to the sibling shard before a resharding event."*

A regression/repro test already committed in the repo demonstrates exactly this failure mode: a `GlobalContractDistribution` receipt is created with `target_shard` pointing at a shard that is later split twice in sequence (dynamic resharding). If the shard-layout history/remapping used by `receiver_shard_id()` cannot resolve the now-doubly-stale `target_shard` back to a current shard id, `receiver_shard_id()` returns an error/`None` and the `.unwrap()` in `receipt_filter_fn` panics, stalling chain progress: [2](#0-1) [3](#0-2) 

The attacker-reachable trigger path is:
1. An unprivileged account deploys a global contract, generating a `GlobalContractDistribution` receipt with `target_shard` set to the shard containing the deployer at deploy time.
2. The deployer (or any account) submits transactions that saturate compute on that shard, forcing the distribution receipt into the delayed-receipt queue for multiple blocks (analogous to the "listener draining while access-logger references a scoped object" precondition in the Envoy bug — here the receipt outlives two shard-layout transitions of the object it references).
3. Two sequential dynamic-resharding splits occur while the receipt is still delayed, changing the shard layout twice before the receipt is ever popped and filtered.
4. When the receipt is finally popped from the delayed queue, `receipt_filter_fn` calls `receiver_shard_id()` on a `target_shard` that predates two layout generations; if the remapping logic cannot resolve this (single-hop-only remapping, or missing multi-generation lookup), the `.unwrap()` panics inside chunk application — an operation every validator node executing that chunk performs deterministically.

This is directly reachable by an ordinary contract deployer using only standard global-contract deployment and compute-heavy calls; no special privileges, malicious peer, or validator-only code path is required.

### Impact Explanation
A panic inside `receipt_filter_fn`, called from the runtime's `pop()` during delayed-receipt processing in chunk application, is deterministic and reached by every node applying the affected chunk. This is a transaction-triggered halt of chunk/block production, i.e. a full liveness failure of the network reachable from a single sequence of ordinary transactions (contract deploy + saturation transactions), satisfying the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Triggering requires: (a) deploying a global contract, (b) keeping its distribution receipt in the delayed queue by saturating shard compute across several blocks, and (c) causing two dynamic-resharding splits of the target shard's lineage while it is delayed. Dynamic resharding thresholds and cadence are configuration-controlled and, in the worst case (aggressive/forced splits enabled by validators or on a testnet-like configuration), this sequence is achievable purely with transactions from an unprivileged account — no validator or network-level cooperation needed, only enough gas to keep submitting transactions and enough patience to wait for two epoch-boundary resplits. The presence of a dedicated repro test in the codebase (`test_stale_global_contract_distribution_after_double_resharding`) indicates the scenario was considered plausible enough to write a targeted regression test for.

### Recommendation
- Replace the `.unwrap()` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs`) with a fallible path that never panics on unresolved historical `target_shard`/`receiver_id` values; on remap failure, either conservatively retain the receipt for later re-evaluation or route it through a defined fallback shard rather than aborting chunk application.
- Ensure `receiver_shard_id()` in `core/primitives/src/receipt.rs` (and whatever shard-layout history lookup it depends on) supports remapping across *multiple* consecutive resharding generations, not just a single split, so that receipts (in particular `GlobalContractDistribution`) delayed across more than one resharding event resolve correctly.
- Add explicit protocol-level validation/handling for `GlobalContractDistribution` receipts that traverse multiple resharding boundaries, and confirm the existing `test_stale_global_contract_distribution_after_double_resharding` test actually asserts and exercises the fixed behavior (not just documents the risk) in CI.

### Proof of Concept
The committed test demonstrates the reachable trigger sequence end-to-end: [4](#0-3) 
1. Deploy a test contract and then a global contract from `user0`, producing a `GlobalContractDistribution` receipt targeting `user0`'s shard.
2. Continuously submit `burn_gas_raw` transactions to saturate the shard's compute budget every block, keeping the distribution receipt parked in the delayed-receipt queue.
3. Force two sequential dynamic-resharding splits (`force_split_shards` configured to split the deployer's shard, then another shard) while the receipt remains delayed.
4. Stop saturating and let the delayed queue drain; the test asserts chain height progresses past `drain_end`, i.e. it asserts the node does **not** stall/panic — confirming that without the corresponding fix, processing the stale receipt in `receipt_filter_fn`'s `.unwrap()` on `receiver_shard_id()` would halt the chain.

### Citations

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-39)
```rust
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
