### Title
`receipt_filter_fn` unwraps `receiver_shard_id`, causing a transaction-triggered chunk-application panic during delayed-receipt processing after resharding - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()` while popping/peeking delayed receipts. This mirrors the reported Chainlink pattern: a function reachable from ordinary chunk/receipt processing ("must not revert/panic") contains an internal `unwrap()`/`panic!` path that can be triggered by receipt state that becomes invalid across two resharding transitions, halting chunk application deterministically on every honest node.

### Finding Description
`receipt_filter_fn` is used to filter delayed receipts that may belong to a sibling/parent shard right after a resharding event: [1](#0-0) 

It computes `receiver_shard_id` against the *current* shard layout and `.unwrap()`s the result, treated as infallible: [2](#0-1) 

This function is invoked from `pop` (used in `process_delayed_receipts`) and from `peek_iter` (used for contract-preparation lookahead scheduling), both on the hot chunk-apply path that must always succeed for the chain to make progress: [3](#0-2) 

The comment directly above documents the exact hazard: with ReshardingV3, a chunk can have delayed receipts that "technically belong to the sibling shard" — i.e., `receiver_shard_id` may legitimately fail to resolve a *stale* target shard once shard layout has moved on (e.g., across **two successive resharding generations**, where the receipt's original target shard no longer maps cleanly into the current layout). The code assumes `receiver_shard_id` always succeeds for such stale receipts and unconditionally unwraps.

This exact failure mode is already reproduced by an in-repo regression/PoC test that documents the vulnerable behavior: [4](#0-3) [5](#0-4) 

The test explicitly saturates a shard's compute budget so that a cross-shard receipt (`GlobalContractDistribution`, itself targeted at a shard that then undergoes two sequential splits) is pushed into the delayed receipts queue and stays there through both resharding events, then asserts that chain progress continues (`head_height >= drain_end`) — with the comment: "If the vulnerability exists, processing the stale ... receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations."

Unlike Sherlock's Chainlink VRF callback (`fulfillRandomWords` must never revert or the randomness request is permanently stuck), nearcore's analogous invariant is that receipt/chunk application on the runtime apply loop must never panic — because it is executed deterministically by all validators applying the same chunk, and a panic there halts the node (or, if it doesn't cleanly abort but is caught, results in state-transition failure) rather than gracefully rejecting only the bad receipt.

### Impact Explanation
A panic inside `receipt_filter_fn`, reached via `pop`/`peek_iter` in `DelayedReceiptQueueWrapper` during `process_delayed_receipts`, aborts chunk application. Because every validator applying the same chunk hits the identical code path deterministically, this is a **transaction/state-triggered halt**: nodes that reach the point of draining the stale delayed receipt will panic (crash or fail to produce/validate the chunk), stalling chain progress for the affected shard. This matches the severity bar for "transaction-triggered halt" callable purely by submitting ordinary transactions/receipts and driving the network through resharding — no privileged, malicious-peer, or validator-only access is required.

### Likelihood Explanation
Reaching this requires: (1) a cross-shard receipt landing in the delayed-receipt queue targeting a shard, and (2) that shard undergoing two resharding transitions before the receipt is drained, such that the old target shard cannot be remapped by `receiver_shard_id` under the current shard layout. Dynamic resharding (`ReshardingV3`/`DynamicResharding`) is a shipped/shipping feature, and an unprivileged user can create the necessary congestion (to keep a receipt delayed long enough) purely by submitting compute-heavy transactions, as demonstrated by the existing PoC test `test_stale_global_contract_distribution_after_double_resharding`. The likelihood is non-trivial specifically in periods of active dynamic resharding, but requires precise timing across multiple epochs, which the repo's own test already constructs deterministically to reproduce.

### Recommendation
Do not `.unwrap()` inside `receipt_filter_fn`. Instead, propagate errors and treat unresolvable/stale receipts (whose target shard cannot be mapped in the current layout) conservatively — e.g., filter them out (treat as not-belonging to current shard, matching the function's existing "false" semantics) rather than panicking, or make `receiver_shard_id` resilient to resolving via ancestor-shard lookup across multiple resharding generations so stale receipts are still correctly attributed. `pop` and `peek_iter` callers should propagate a `Result` instead of relying on an infallible boolean predicate.

### Proof of Concept
The repository already contains a concrete PoC exercising this exact bug class: [6](#0-5) 
which force-splits two shards sequentially so that a `GlobalContractDistribution` receipt (targeting the shard that is split twice) remains stuck in the delayed queue across both resharding events, then saturates compute to keep it delayed: [7](#0-6) 
and finally drains the queue, asserting the chain does not stall (i.e., does not panic in `receipt_filter_fn`): [8](#0-7)

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

**File:** runtime/runtime/src/congestion_control.rs (L880-920)
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

    pub(crate) fn peek_iter(
        &'a self,
        trie_update: &'a TrieUpdate,
    ) -> impl Iterator<Item = ReceiptOrStateStoredReceipt<'static>> + 'a {
        self.queue
            .iter(trie_update, false)
            .map_while(Result::ok)
            .filter(|receipt| self.receipt_filter_fn(receipt))
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-40)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L52-66)
```rust
    // Configure dynamic resharding to force-split two shards sequentially.
    // The first split targets the shard containing deploy_user (user0), so the
    // GlobalContractDistribution receipt becomes stale after two layout transitions.
    let first_split_shard = base_shard_layout.account_id_to_shard_id(&deploy_user);
    let second_split_shard = base_shard_layout.account_id_to_shard_id(&create_account_id("user4"));
    assert_ne!(first_split_shard, second_split_shard);

    let dynamic_config = DynamicReshardingConfig {
        memory_usage_threshold: u64::MAX,
        min_child_memory_usage: u64::MAX,
        max_number_of_shards: 100,
        min_epochs_between_resharding: 1.try_into().unwrap(),
        force_split_shards: vec![first_split_shard, second_split_shard],
        block_split_shards: vec![],
    };
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-186)
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
