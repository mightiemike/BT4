Based on my investigation, I found a strong analog to CVE-2022-34675's bug class (unchecked return value / unguarded error path leading to null-like dereference and denial of service).

### Title
Unchecked `.unwrap()` on `receiver_shard_id()` in delayed-receipt filtering can panic and halt chunk production - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` without handling the `Err` case that `receiver_shard_id` can legitimately return.

### Finding Description
`receiver_shard_id` on `GlobalContractDistribution` receipts calls `shard_layout.resolve_to_current_shard(target_shard)`, returning `Err(EpochError::ShardingError(..))` if the receipt's stale `target_shard` cannot be resolved to any shard in the current layout or its split history [1](#0-0) . `receipt_filter_fn`, used by both `pop` and `peek_iter` in the delayed-receipt queue processed on every `apply()` call, unconditionally `.unwrap()`s this result [2](#0-1) . Any `Err` here becomes a panic during normal chunk apply, since `pop()` calls `receipt_filter_fn` inside the delayed-receipt draining loop that every honest chunk producer runs [3](#0-2) . `GlobalContractDistribution` receipts are created by an ordinary `DeployGlobalContract` action from any unprivileged account and can be delayed in the queue across multiple resharding events. The repository already contains a regression test, `test_stale_global_contract_distribution_after_double_resharding`, explicitly built to reproduce this exact panic scenario (double resharding stranding a `GlobalContractDistribution` receipt's `target_shard`) and its own comments state "the fix only works with V3 shard layouts (dynamic resharding)... With static resharding, the shard layout doesn't maintain a full split history" [4](#0-3) [5](#0-4) . This means the panic is understood to be resolved only for the `ShardLayout::V3` split-history path; the underlying `.unwrap()` in `receipt_filter_fn` itself remains unguarded, and any code path where `resolve_to_current_shard` fails to find a valid mapping (e.g., legacy/static shard layout transitions that predate full split-history tracking, or any future shard layout change that isn't a simple split, such as a merge) will still hit this same panic.

### Impact Explanation
A panic inside `receipt_filter_fn`, reached from `DelayedReceiptQueueWrapper::pop`/`peek_iter` during `apply()`, aborts chunk processing for the affected shard. Since `apply()` runs on every honest chunk-producing and validating node processing that shard, this is a transaction-triggered halt of chunk production for that shard — matching the CVE class of an unchecked/unhandled error condition causing denial of service, elevated here from "driver DoS" to "consensus-shard DoS."

### Likelihood Explanation
Reaching this bug requires only an unprivileged transaction (a `DeployGlobalContract` action) whose resulting `GlobalContractDistribution` receipt becomes delayed across a shard-layout transition where `resolve_to_current_shard` cannot map the stale `target_shard`. The presence of a dedicated regression test in the codebase for the double-resharding case, with the explicit caveat that the fix doesn't cover static/non-V3 resharding, indicates the underlying `.unwrap()` is a known-fragile point that the current fix narrows rather than eliminates.

### Recommendation
Replace `.unwrap()` in `receipt_filter_fn` with proper error propagation (return a `Result` from the filter, and have callers of `pop`/`peek_iter` bubble up a `RuntimeError`/`StorageError` instead of panicking), and audit `resolve_to_current_shard` for all historical/legacy shard-layout transition types (not just `V3` splits) to ensure it can always resolve a valid current shard or return a handled error.

### Proof of Concept
1. As any account, submit a `DeployGlobalContract` transaction, generating an incoming `GlobalContractDistribution` receipt targeting the sender's current shard.
2. Saturate compute on that shard so the receipt is pushed into the delayed-receipt queue (as demonstrated by `test_stale_global_contract_distribution_after_double_resharding`) [6](#0-5) .
3. Trigger a shard-layout transition through which `resolve_to_current_shard` cannot map the receipt's stale `target_shard` (the test uses two sequential dynamic-resharding splits; other transition types, e.g. legacy/static resharding, are explicitly called out as unresolved by the current fix).
4. When the delayed queue drains and `pop()`/`peek_iter()` invoke `receipt_filter_fn` on this receipt, `receiver_shard_id(..).unwrap()` panics, halting chunk processing for the shard [7](#0-6) .

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

**File:** runtime/runtime/src/congestion_control.rs (L874-910)
```rust
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-152)
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
