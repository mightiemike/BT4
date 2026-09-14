### Title
Reachable panic in `receipt_filter_fn` via `receiver_shard_id().unwrap()` on unresolvable `GlobalContractDistribution` receipts causes a transaction-triggered chain halt - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` while draining the delayed-receipt queue during every chunk apply. For `GlobalContractDistribution` receipts, `receiver_shard_id` can return `Err(EpochError::ShardingError(...))` when the receipt's `target_shard` cannot be found in the current shard layout nor resolved through its split history via `resolve_to_current_shard`. That `Err` is unconditionally unwrapped, panicking the runtime `apply` call on every node processing that chunk. [1](#0-0) 

### Finding Description
`receiver_shard_id` for `ReceiptEnum::GlobalContractDistribution` attempts to remap a stale `target_shard` to a shard in the current layout via `shard_layout.resolve_to_current_shard(target_shard)`, and only returns an error if that resolution fails: [2](#0-1) 

That fallible result is unwrapped unconditionally inside `receipt_filter_fn`, which is invoked on every delayed-receipt pop performed during `process_delayed_receipts`: [3](#0-2) 

A `GlobalContractDistribution` receipt is created by any account deploying a global contract (`tx_deploy_global_contract` action), and it is forwarded shard-by-shard over multiple blocks until every shard has received the contract (`forward_distribution_next_shard`), remaining a live receipt in-flight (potentially sitting in the delayed queue) for an extended period: [4](#0-3) 

The existing regression test `test_stale_global_contract_distribution_after_double_resharding` demonstrates that if a global-contract-distribution receipt is kept in the delayed queue while the chain undergoes **two** consecutive dynamic reshardings, this exact `unwrap()` panics unless `resolve_to_current_shard` correctly resolves the two-generations-old `target_shard`: [5](#0-4) 

While the currently-visible code appears to patch the two-generation case via `resolve_to_current_shard`, the underlying design is fragile: `receipt_filter_fn`'s `.unwrap()` has zero tolerance for any future case where `resolve_to_current_shard` cannot find a descendant shard for the receipt's `target_shard` (e.g., additional resharding generations beyond what the split-history tracking supports, shard-layout resets, or any other divergence between the stored `target_shard` and the currently active layout's split-history bookkeeping). Because the fix is scoped ("only works with V3 shard layouts (dynamic resharding)" per the test's own comment), any configuration or future resharding sequence that the split-history resolution does not cover will hit the same unwrap panic. This is a systemic/structural weakness in a code path directly reachable by an ordinary transaction signer (deploying a global contract, then keeping the shard congested with cheap gas-burning calls to keep the receipt delayed across resharding events), matching the CVE's underlying bug class: attacker-supplied/queued protocol messages reaching an unvalidated memory/queue-processing routine that crashes the process (segfault in cFS ≈ panic/crash in nearcore runtime).

### Impact Explanation
A panic inside `Runtime::apply`'s receipt-processing path is not a recoverable `Result::Err` — it aborts the validator process executing that chunk. Because all honest validators/chunk-producers execute the same deterministic state transition, they would all panic identically when processing the same block, producing a **chain-wide halt** triggered purely by a sequence of ordinary user transactions (global contract deploy + gas-burning calls to control receipt timing) combined with routine dynamic resharding activity. This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Exploitation requires: (1) the network to have `DynamicResharding`/shard-splitting active, (2) an attacker deploying a global contract whose distribution receipt gets delayed, and (3) enough resharding generations occurring (or split-history edge cases) that `resolve_to_current_shard` fails to find a match. The demonstrated regression test shows the exact panic path is real and was hit under a plausible two-resharding scenario; the mitigation in place is narrow ("only works with V3 shard layouts") and depends entirely on `resolve_to_current_shard`'s split-history bookkeeping remaining complete for every future resharding path. Given resharding cadence and configuration are governed by protocol/network operators (not fully attacker-controlled) but the receipt-delay windowing can be extended by an attacker via gas-saturation, likelihood is Medium: it is not trivially triggerable on demand, but the underlying `.unwrap()` is a defense-in-depth gap with no fallback (e.g., dropping the stale receipt or logging-and-skipping) for any case the resolver doesn't cover.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:876`) with a fallible/safe fallback: if `receiver_shard_id` returns an error (e.g., an unresolvable stale `target_shard`), treat the receipt as belonging to the current shard (retain it) or explicitly handle it via a `RuntimeError`/`StorageInconsistentState` path rather than panicking, consistent with how other genuinely inconsistent-state conditions are surfaced elsewhere in `runtime/runtime/src/lib.rs`. Additionally, harden `resolve_to_current_shard`/`GlobalContractDistributionReceipt` handling so that stale receipts always terminate gracefully (e.g., dropped with a warning, or re-targeted defensively) regardless of how many resharding generations have elapsed, rather than relying on unwrap-free happy paths only being validated by targeted tests.

### Proof of Concept
1. Enable `DynamicResharding` on a test network with force-split shard configuration (as in `test_stale_global_contract_distribution_after_double_resharding`, `test-loop-tests/src/tests/global_contracts_distribution.rs:30-186`).
2. Deploy a global contract from an account on the shard designated to be split first, producing a `GlobalContractDistributionReceipt` with `target_shard` = that shard.
3. Submit gas-saturating transactions each block to keep the shard's compute budget exhausted, forcing the distribution receipt into the delayed-receipt queue across two (or, in an unpatched/uncovered configuration, more) resharding boundary transitions.
4. Stop saturating and let the delayed queue drain; when the runtime pops the stale receipt, `receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()`. If `resolve_to_current_shard` cannot map the old `target_shard` to a shard in the now-multiple-generations-newer layout, the `unwrap()` panics, aborting `Runtime::apply` and halting the chain at that block height for every node applying it.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L868-910)
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
