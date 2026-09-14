### Title
`receipt_filter_fn` panics via `.unwrap()` on `receiver_shard_id` when a stale `GlobalContractDistribution` receipt's target shard cannot be resolved through the shard-split history - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the `Result` returned by `Receipt::receiver_shard_id`, which can return an `Err(EpochError::ShardingError(...))` for a `GlobalContractDistribution` receipt whose recorded `target_shard` can no longer be resolved to a current shard.

### Finding Description
`Receipt::receiver_shard_id` computes the destination shard for a receipt. For most receipt kinds it is a simple account-to-shard mapping, but for `ReceiptEnum::GlobalContractDistribution` it must remap a possibly-stale `target_shard` (recorded at the time `DeployGlobalContractAction`/`initiate_distribution` created the receipt) to the current shard layout via `ShardLayout::resolve_to_current_shard`, which walks `shards_split_map`: [1](#0-0) 

`resolve_to_current_shard` returns `None` — turned into `Err(EpochError::ShardingError(...))` by the caller — only when the shard id is absent from *both* the current layout *and* the retained split history: [2](#0-1) 

The consumer of this fallible function inside the hot chunk-apply path unwraps it without any error handling: [3](#0-2) 

This `receipt_filter_fn` is invoked from `DelayedReceiptQueueWrapper::pop` and `peek_iter`, both on the per-chunk delayed-receipt processing path used by every apply call: [4](#0-3) 

This is structurally the same bug class as the Rio report: a receipt/operation is created referencing a resource (a target strategy in Rio; a target shard here) that is valid at creation time. If that resource later becomes unreachable through the normal resolution path — the strategy is de-whitelisted in Rio, or (in nearcore) the shard is spun through more resharding generations than are retained in the split-history map, or the shard-layout history available at the point of lookup doesn't cover the receipt's original generation — the code that assumed success unconditionally unwraps and blows up instead of handling the failure gracefully.

The repository already contains a regression test explicitly built around one instance of this exact scenario — two sequential dynamic-resharding splits causing a `GlobalContractDistribution` receipt sitting in the delayed queue to become "stale" — and the test's own comment states the failure mode directly: [5](#0-4) 

That test currently passes because `resolve_to_current_shard`'s split-history walk (fed by `get_shard_layout_history`) happens to cover the two generations exercised there. However, `receipt_filter_fn`'s `.unwrap()` remains a live landmine: `resolve_to_current_shard` is explicitly documented to return `None` "only if the shard ID is absent from both the current layout and the split history" — i.e., it is not a total function, and the split-history depth it relies on is bounded by whatever `get_shard_layout_history` supplies to `derive_v3`, not by an unconditionally-complete lineage. Any situation where a `GlobalContractDistribution` receipt is delayed across enough resharding generations (or across a chain of reshardings where intermediate history isn't retained/available at lookup time — e.g. very deep congestion causing it to sit in the delayed queue for many epochs while resharding repeats) reaches the `None`/`Err` branch and the `unwrap()` panics.

### Impact Explanation
A panic inside `receipt_filter_fn`, reached from `DelayedReceiptQueueWrapper::pop`/`peek_iter` during ordinary chunk application (`process_delayed_receipts`), aborts the chunk-apply flow. Since every honest node applies the same delayed receipt in the same deterministic order, this panic is deterministic and reproducible across all validators tracking that shard — this is a transaction/receipt-triggered halt of shard processing, not a node-specific fault. Because a `DeployGlobalContractAction` (any account can submit this) is what creates the `GlobalContractDistribution` receipt in the first place, an ordinary, permissionless transaction plants the receipt whose eventual processing (after enough resharding activity) can crash chunk application network-wide for the affected shard.

### Likelihood Explanation
Triggering requires the receipt to remain in the delayed queue while several resharding generations occur (congestion via the same technique the existing regression test uses — saturating compute in the target shard to force the receipt to stay delayed across resharding boundaries) and for the split-history lookup to not fully cover the generations elapsed. Dynamic resharding is an in-scope, increasingly-used feature; the existing test only proves the 2-generation case is currently handled, not that arbitrarily deep/discontinuous resharding histories are always covered by `get_shard_layout_history`. This is a plausible, deterministically-reachable condition rather than a purely theoretical one, but it depends on specific resharding-history retention behavior that would need further validation to pin down the exact minimum number of generations/gaps required to hit the `None` branch.

### Recommendation
Change `receipt_filter_fn` to propagate the `Result` from `receiver_shard_id` instead of unwrapping it (mirroring how `forward_from_buffer_to_shard` already does `receiper_shard_id(&shard_layout)?`), and have `pop`/`peek_iter`/their callers handle the error path without panicking — e.g. by returning a `RuntimeError`/`StorageError::StorageInconsistentState` that is surfaced through normal error handling, or by ensuring `get_shard_layout_history`/`shards_split_map` retention is guaranteed to cover every generation a delayed receipt can span before it is ever popped.

### Proof of Concept
The repository's own regression test demonstrates the mechanism (force two sequential dynamic-resharding splits while saturating compute on the shard holding a delayed `GlobalContractDistribution` receipt, then draining the queue) and states in comments that "processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations" if the underlying fix/history coverage is insufficient: [6](#0-5) [5](#0-4) 

Extending this scenario to more resharding generations, or to a history-retention gap not covered by `get_shard_layout_history`, reaches the `unwrap()` panic at: [7](#0-6)

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

**File:** runtime/runtime/src/congestion_control.rs (L868-879)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-100)
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

    let epoch_length: BlockHeightDelta = 10;
    let base_boundary_accounts = create_account_ids(["user2", "user3"]).to_vec();
    let base_shard_layout = ShardLayout::multi_shard_custom(base_boundary_accounts, 3);
    let deploy_user: AccountId = create_account_id("user0");
    let users = create_account_ids(["user0", "user1", "user2", "user3", "user4", "user5"]).to_vec();
    let validators_spec = create_validators_spec(1, 0);
    let clients = validators_spec_clients(&validators_spec);
    let chunk_producer = clients[0].clone();
    let gas_limit = Gas::from_teragas(300);
    let base_pv = PROTOCOL_VERSION - 1;

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

    let genesis = TestLoopBuilder::new_genesis_builder()
        .protocol_version(base_pv)
        .validators_spec(validators_spec)
        .shard_layout(base_shard_layout.clone())
        .epoch_length(epoch_length)
        .gas_limit(gas_limit)
        .add_user_accounts_simple(&users, Balance::from_near(1_000_000))
        .build();
    let base_epoch_config = TestEpochConfigBuilder::from_genesis(&genesis).build();

    let mut dynamic_epoch_config = base_epoch_config.clone();
    dynamic_epoch_config.shard_layout_config =
        ShardLayoutConfig::Dynamic { dynamic_resharding_config: dynamic_config };

    let epoch_config_store = EpochConfigStore::test(BTreeMap::from([
        (base_pv, Arc::new(base_epoch_config)),
        (base_pv + 1, Arc::new(dynamic_epoch_config)),
    ]));

    let mut env = TestLoopBuilder::new()
        .genesis(genesis)
        .clients(clients)
        .epoch_config_store(epoch_config_store)
        .gc_num_epochs_to_keep(5)
        .build();

    // Step 1: Deploy the test contract on user0's account so we can call burn_gas_raw.
    {
        let node = env.node_for_account(&chunk_producer);
        let tx = node.tx_deploy_test_contract(&deploy_user);
        node.submit_tx(tx);
    }
    env.runner_for_account(&chunk_producer).run_for_number_of_blocks(2);
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
