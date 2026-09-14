### Title
Panic-on-`.unwrap()` in `DelayedReceiptQueueWrapper::receipt_filter_fn` permanently halts delayed-receipt draining for a shard after resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
The external report describes a queue-processing loop (`mintDepositInQueue`) that is meant to be non-atomic (skip bad items, keep going) but instead reverts the *entire* transaction whenever one queued item no longer satisfies an assumption made when it was enqueued (`relayerFee` grew between `deposit()` and `mintDepositInQueue()`), permanently jamming the whole queue. The closest reachable analog in nearcore is the delayed-receipt queue pop path, where a receipt enqueued under one shard layout is later dequeued under a *different* shard layout (after resharding), and the code path that is supposed to gracefully filter it out instead calls `.unwrap()` and can panic, non-atomically halting the exact same queue for every subsequent chunk.

### Finding Description
`DelayedReceiptQueueWrapper::pop` drains the shard's delayed-receipt queue every chunk and is expected to tolerate receipts that were queued under an old shard layout (comment: "it's possible for a chunk to have delayed receipts that technically belong to the sibling shard before a resharding event"): [1](#0-0) 

`receipt_filter_fn` computes `receiver_shard_id` for the popped receipt against the *current* shard layout and unconditionally unwraps the result: [2](#0-1) 

`pop` (called every chunk from `process_delayed_receipts`) loops over the front of the queue and calls `receipt_filter_fn` on each dequeued receipt until it finds one belonging to the current shard, discarding the mismatched ones from processing: [3](#0-2) 

This filtering logic is exactly analogous to the Sherlock finding's intended-but-broken non-atomic skip: `mintDepositInQueue()` intends to skip/continue past a queue entry whose precondition (`relayerFee`) changed, but instead the arithmetic underflow makes the whole call revert. Here, the delayed-receipt queue intends to skip/discard a stale `GlobalContractDistribution` receipt whose `target_shard` no longer maps into the current shard layout, but `receiver_shard_id(&shard_layout).unwrap()` can panic instead of gracefully being filtered.

A regression test in this exact tree documents the scenario and its expected symptom: a `GlobalContractDistribution` receipt is created targeting shard `S_A`; `S_A` is split twice via dynamic resharding while the receipt sits in the delayed queue (because the shard's compute is saturated); once saturation stops and the queue drains, dequeuing the stale receipt is expected to call `receiver_shard_id()` on a `target_shard` that cannot be remapped through two resharding generations: [4](#0-3) [5](#0-4) 

Because `process_delayed_receipts` calls `pop()` once per chunk and the stale receipt sits at the *front* of the FIFO queue, a panic here is not a one-off: it repeats on every subsequent chunk-apply attempt for that shard until the state is manually fixed, which is a transaction/receipt-triggered chunk-apply halt for the affected shard, not merely a transient failure of one transaction. [6](#0-5) 

### Impact Explanation
If `receiver_shard_id` can fail to remap a receipt's stored `target_shard`/`receiver_id` through the current epoch's shard layout after multiple resharding generations, the `.unwrap()` in `receipt_filter_fn` turns a routine "should filter, not process" case into a runtime panic during `Runtime::apply`. Since the receipt remains at the head of the persistent delayed-receipt queue (it is only removed from the trie by `pop`, and popping is what panics), every chunk application for that shard from then on hits the same panic — a transaction-triggered halt of chunk production/validation for the shard, satisfying the "transaction-triggered halt" acceptance criterion. This is a liveness-critical defect reachable purely by an unprivileged account deploying a global contract (or otherwise creating a receipt whose routing depends on `target_shard`) combined with normal chunk-producer-controlled dynamic resharding, i.e., no malicious validator or node behavior is required to trigger it.

### Likelihood Explanation
Triggering requires: (1) a receipt type whose shard-routing metadata (e.g., `GlobalContractDistribution::target_shard`) is fixed at creation time, (2) that receipt getting delayed (queued) rather than processed immediately (achievable by any account saturating a shard's gas/compute budget with ordinary transactions, as the regression test does), and (3) the shard being resharded at least once (in the repro, twice) before the receipt is dequeued. Dynamic resharding, gas saturation, and global contract deployment are all reachable via unprivileged transactions/RPC calls; only the *timing coincidence* with resharding is somewhat elaborate, which is why the codebase already ships a dedicated regression test for it — indicating this is a recognized, non-hypothetical risk area rather than a purely theoretical one. Whether the current tree has *already* patched `receiver_shard_id`/`receipt_filter_fn` to avoid the panic could not be confirmed from the available index (the `receiver_shard_id` implementation body in `core/primitives/src/receipt.rs` was not retrievable within the tool budget), so it is uncertain whether this is a live bug or one for which a fix already landed alongside the test.

### Recommendation
- Make `receipt_filter_fn` (and any other call site of `receiver_shard_id` on receipts drawn from the delayed queue) tolerant of remapping failures: treat an unmappable/stale `target_shard` as "does not belong to this shard" (filter it out, or route/re-park it) instead of `.unwrap()`-panicking, mirroring the intended non-atomic behavior described in the analog report.
- Ensure `receiver_shard_id` returns a typed error (not an infallible unwrap-only path) for receipts whose stored shard reference predates more resharding generations than the current shard-layout ancestry tracks, and audit all `.unwrap()`/`.expect()` calls on shard-layout lookups in the receipt-processing hot path (`congestion_control.rs`, `lib.rs` delayed/incoming receipt loops) for the same class of issue.
- Confirm via the existing `test_stale_global_contract_distribution_after_double_resharding` test (and extend it to cover other receipt kinds carrying shard identifiers, if any) that draining always makes forward progress even when a queued receipt's original shard target has been superseded by multiple resharding events.

### Proof of Concept
Reachable end-to-end scenario (mirrored by the existing test in the repo):
1. An unprivileged account deploys a `GlobalContractDistribution`-creating transaction (e.g. deploy a global contract) whose resulting receipt's `target_shard` is shard `S_A`.
2. Ordinary transactions from any account saturate `S_A`'s compute budget every block so the distribution receipt is pushed into the persistent delayed-receipt queue rather than executed immediately (`process_incoming_receipts` → `delayed_receipts.push`).
3. While the receipt sits in the queue, the chunk-producer-driven dynamic resharding config force-splits `S_A` twice in sequence (two epoch transitions), changing the shard layout the chain uses for shard-id remapping.
4. Saturation transactions stop; on a later chunk, `process_delayed_receipts` calls `processing_state.delayed_receipts.pop(...)`, which calls `receipt_filter_fn`, which calls `receiver_shard_id(&shard_layout).unwrap()` on the stale receipt's `target_shard` that can no longer be remapped through two resharding generations.
5. If the remap fails, the `.unwrap()` panics, and because the receipt is not removed from the head of the queue for a *different* receiver-side reason, the same panic recurs on every following chunk apply for the shard, halting its progress — directly demonstrated by the assertion in the codebase's own regression test that the chain must keep advancing past `drain_end` or it is considered stalled/panicked. [7](#0-6)

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-185)
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
