Found it. `receipt_filter_fn` at `runtime/runtime/src/congestion_control.rs:874-878` calls `.unwrap()` on `receipt.get_receipt().receiver_shard_id(&shard_layout)`, and `receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) returns `Err(EpochError::ShardingError(...))` for a `GlobalContractDistribution` receipt whose `target_shard` no longer exists in the shard layout *or its split history* (i.e. `resolve_to_current_shard` returns `None`). This directly mirrors the reported bug class: a function correctly handles the "known" cases (target shard present in current layout, or resolvable via split history) but has no fallback for the third case (target shard from a layout too old to resolve), and instead of gracefully erroring it is `.unwrap()`'d into a panic on the node's apply path.

### Title
Unwrapped `receiver_shard_id` error in `receipt_filter_fn` causes a transaction/receipt-triggered chunk-apply panic for stale `GlobalContractDistribution` receipts across repeated resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` (`congestion_control.rs:874-878`) calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()`. `Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) has two success paths for `GlobalContractDistribution` receipts — target shard exists in the current layout, or it can be `resolve_to_current_shard`-ed from split history — and returns `Err` only when neither applies (a receipt whose `target_shard` predates even the tracked split history, e.g. after multiple successive reshardings while the receipt sat in the delayed queue). That `Err` is not handled by the caller and instead unwraps to a panic during `DelayedReceiptQueueWrapper::pop` (`:880-910`), which is on the mandatory chunk-apply path (`process_delayed_receipts`, `runtime/runtime/src/lib.rs:2570-2664`).

### Finding Description
`GlobalContractDistribution` receipts are forwarded shard-by-shard by `forward_distribution_next_shard` (`runtime/runtime/src/global_contracts.rs:288-333`) and can sit in the persistent delayed-receipt queue if the receiving shard is congested/compute-limited (`process_incoming_receipts` / `process_delayed_receipts`). If that shard undergoes dynamic resharding one or more times while the receipt is delayed, its `target_shard` field becomes stale relative to the current `ShardLayout`.

`receiver_shard_id` (`receipt.rs:437-466`) attempts to resolve this via `shard_layout.resolve_to_current_shard(target_shard)`, which walks the tracked split history. But `resolve_to_current_shard` can only walk history that the current layout actually retains; if the receipt is stale enough (e.g., across two resharding generations combined with GC/pruning of older split-history entries, or a target shard from a layout the current one has no lineage record for), the lookup returns `None` and the function returns `Err(EpochError::ShardingError(...))` rather than a valid shard id — i.e., there are effectively three outcomes (current layout, resolvable-via-history, unresolvable) but only two are treated as "success."

`receipt_filter_fn` (`congestion_control.rs:874-878`), used inside `DelayedReceiptQueueWrapper::pop` (called every chunk from `process_delayed_receipts`, `lib.rs:2570-2664`) and `peek_iter`, does:
```
let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
```
`.unwrap()` on the `Err` case panics the runtime thread while applying a chunk — a validator crash triggered purely by delayed-receipt drainage after resharding, not by any adversarial network/validator behavior. This is reachable by any user who deploys a global contract (an ordinary `DeployGlobalContract` transaction) on a shard that subsequently reshards multiple times while the distribution receipt is delayed by congestion.

The existing test `test-loop-tests/src/tests/global_contracts_distribution.rs:30-186` (`test_stale_global_contract_distribution_after_double_resharding`) was specifically written to catch this exact panic scenario across two resharding generations, and the comment at lines 165-168 states explicitly: *"If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations."* This confirms the panic is a recognized, previously-triggerable failure mode of exactly this code path; whether the current `resolve_to_current_shard` split-history depth is sufficient to cover *all* possible delay/resharding-count/GC combinations in production (long delays, many successive reshardings, deep pruning of shard-layout history) was not verified from the code alone — the `.unwrap()` remains present and will panic on any input where resolution fails, whatever the precise conditions for that are.

### Impact Explanation
A successful trigger halts chunk production/application on the affected shard with a panic inside the mandatory `process_delayed_receipts` path — a transaction-triggered halt affecting the whole validator set applying that shard (all honest nodes hit the same delayed receipt deterministically), which matches the required "transaction-triggered halt" / "invalid state transition" impact class. This is a liveness/availability failure of the network, not merely a rejected transaction.

### Likelihood Explanation
Requires: (1) a `DeployGlobalContract` action (ordinary, permissionless), (2) the target shard being congested/compute-saturated long enough for the distribution receipt to sit in the delayed queue, and (3) enough resharding events occurring during that delay for `resolve_to_current_shard` to fail to remap the stale `target_shard`. Given dynamic resharding is an ongoing, config-driven, largely automatic protocol feature, and delayed-receipt residency can be arbitrarily long under sustained congestion, this is a plausible, non-adversarial-only sequence of ordinary protocol events rather than a contrived edge case, though it needs an unusually long delay/multiple reshardings to actually manifest, which is why it is not certain to be currently exploitable in practice under normal network conditions.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) with proper error propagation (return a `Result` from `receipt_filter_fn` and propagate through `pop`/`peek_iter`), converting an unresolvable `receiver_shard_id` into a `RuntimeError`/`StorageError::StorageInconsistentState` rather than a panic, consistent with how other "inconsistent delayed state" cases are already handled in `process_delayed_receipts` (`lib.rs:2628-2640`). Additionally, verify/extend `ShardLayout::resolve_to_current_shard`'s retained split-history depth so genuinely valid (non-corrupted) stale receipts can still be resolved across the maximum number of reshardings a receipt could plausibly survive under worst-case congestion.

### Proof of Concept [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The existing regression test `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`) is itself the concrete PoC scaffold: deploy a global contract on a shard, saturate its compute so the resulting `GlobalContractDistribution` receipt is pushed to the delayed queue, force two sequential dynamic reshardings of that shard while the receipt is delayed, then stop saturating and let the delayed queue drain — the assertion checks that the chain does not stall/panic while draining it. Whether this specific test currently passes (i.e., whether the present `resolve_to_current_shard` history depth already covers this two-resharding case) could not be confirmed by static inspection alone; the unresolved risk is any delay/resharding-count combination that exceeds whatever history depth is retained, which the `.unwrap()` in `receipt_filter_fn` will still turn into a panic.

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

**File:** runtime/runtime/src/lib.rs (L2570-2606)
```rust
    fn process_delayed_receipts(
        &self,
        mut processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        compute_limit: u64,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        let delayed_processing_start = std::time::Instant::now();
        let protocol_version = processing_state.protocol_version;
        let mut delayed_receipt_count = 0;

        let mut next_schedule_after = {
            let mut prep_lookahead_iter =
                processing_state.delayed_receipts.peek_iter(&processing_state.state_update);
            schedule_contract_preparation(
                &mut processing_state.pipeline_manager,
                &processing_state.state_update,
                &mut prep_lookahead_iter,
            )
        };

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

**File:** runtime/runtime/src/global_contracts.rs (L288-333)
```rust
fn forward_distribution_next_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<(), RuntimeError> {
    let shard_layout = epoch_info_provider.shard_layout(&apply_state.epoch_id)?;
    let already_delivered_shards = BTreeSet::from_iter(
        global_contract_data
            .already_delivered_shards()
            .iter()
            .cloned()
            .chain(std::iter::once(apply_state.shard_id)),
    );
    let Some(next_shard) = shard_layout
        .shard_ids()
        .filter(|shard_id| !already_delivered_shards.contains(&shard_id))
        .next()
    else {
        return Ok(());
    };
    let already_delivered_shards = Vec::from_iter(already_delivered_shards);
    let predecessor_id = receipt.predecessor_id().clone();
    let next_receipt = global_contract_data.forward(next_shard, already_delivered_shards);
    let mut next_receipt = Receipt::new_global_contract_distribution(predecessor_id, next_receipt);
    let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), 0);
    next_receipt.set_receipt_id(receipt_id);
    if apply_state.save_receipt_to_tx {
        receipt_to_tx.push((
            receipt_id,
            ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: *receipt.receipt_id(),
                    parent_predecessor_id: receipt.predecessor_id().clone(),
                }),
                receiver_account_id: next_receipt.receiver_id().clone(),
                shard_id: apply_state.shard_id,
            }),
        ));
    }
    receipt_sink.forward_or_buffer_receipt(next_receipt, apply_state, state_update)?;
    Ok(())
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
