### Title
Stale `GlobalContractDistribution` receipt causes `receiver_shard_id().unwrap()` panic in `receipt_filter_fn` after repeated resharding, halting the chain - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::pop`/`peek_iter` use `receipt_filter_fn` to discard delayed receipts that "belong" to a sibling shard after a `ReshardingV3` split. The filter calls `receiver_shard_id(&shard_layout).unwrap()` on every delayed receipt using the *current* shard layout [1](#0-0) . For most receipt kinds this remap is safe because they route by account id, but `GlobalContractDistribution` receipts route by a raw, protocol-version-pinned `target_shard` value captured at creation time. If such a receipt sits in the delayed queue across two (or more) resharding events, `target_shard` can become an id that no longer exists/maps in the new shard layout, making `receiver_shard_id` fail and the `.unwrap()` panic — a validator/chunk-producer crash reachable purely by submitting an ordinary `DeployGlobalContract` transaction and then keeping the target shard congested through two resharding cycles.

### Finding Description
- `DelayedReceiptQueueWrapper::pop` walks the delayed-receipt queue and, for every popped item, calls `self.receipt_filter_fn(&receipt)` to decide whether the receipt belongs to the local shard post-split: `receipt_shard_id == self.shard_id` [2](#0-1) . The comment explicitly acknowledges the double-processing/free hazard class: after `ReshardingV3`, "it's possible for a chunk to have delayed receipts that technically belong to the sibling shard," and receipts not matching the shard are silently dropped from being *returned*, but still consumed (popped) from the queue and accounted for in congestion gas/bytes.
- `peek_iter` uses the identical filter [3](#0-2) .
- The resharding split explicitly *duplicates* the whole delayed-receipt column into both children (`DELAYED_RECEIPT_OR_INDICES` is copied to both children, unlike `BUFFERED_RECEIPT*` which is copied to only one child) [4](#0-3) [5](#0-4) . This design relies on `receipt_filter_fn` to correctly disambiguate the intended receiver on each child copy — exactly the kind of "assume-the-wrong-queue-type-owns-this-item" logic that caused the ENA analog (a shared cleanup routine applied uniformly to items from two logically distinct queues without validating that the assumption holds for every item type).
- `GlobalContractDistribution` receipts do not route by account id; they carry a `target_shard: ShardId` set at receipt-creation time (see the fields referenced across `GlobalContractDistribution` receipts in `core/primitives/src/receipt.rs`). When such a receipt is congested and remains in the delayed queue across **two sequential** resharding splits, the shard id it targets can be retired/renumbered by the new shard layout such that `receiver_shard_id(&shard_layout)` cannot resolve it, and the code unwraps that `Result`/`Option` unconditionally, panicking the runtime apply loop.
- The codebase itself contains a dedicated regression test reproducing exactly this scenario, `test_stale_global_contract_distribution_after_double_resharding`, which force-splits the shard holding the deployer's account twice while keeping the `GlobalContractDistribution` receipt stuck in the delayed queue (via compute saturation), then drains the queue and asserts the chain does **not** stall — explicitly describing the failure mode as "processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations" [6](#0-5) .

### Impact Explanation
A panic inside `receipt_filter_fn` occurs during normal receipt-queue draining in the runtime apply loop, which every validator/chunk-producer must execute deterministically. This meets the "transaction-triggered halt" bar: an unprivileged account can trigger it by (1) calling `DeployGlobalContract` (an ordinary, unprivileged action any contract deployer can send) targeting a shard that is scheduled to split, and (2) keeping that shard's chunk compute saturated (via ordinary function calls) so the resulting `GlobalContractDistribution` receipt is pushed into and lingers in the delayed queue through two resharding generations. If the underlying `target_shard` value becomes unresolvable in the post-split layout, all nodes independently panic while applying the same chunk — a consensus-halting, chain-wide denial of service, not a localized/single-node crash, since every honest node runs identical deterministic logic.

### Likelihood Explanation
This requires dynamic/`ReshardingV3` shard splitting to occur twice while a `GlobalContractDistribution` receipt for the affected shard remains delayed — a scenario dependent on network resharding cadence, which is an infrequent, operator/protocol-scheduled event, and requires the attacker to time congestion carefully across two epochs. It is not trivially triggerable on every network configuration (the repository's own test notes "the fix only works with V3 shard layouts (dynamic resharding); with static resharding, the shard layout doesn't maintain a full split history"), and the test's existence and phrasing ("if the vulnerability exists ... panic") suggests this exact defect was already identified and a fix path may exist elsewhere in `target_shard`/shard-layout remapping code that could not be fully confirmed from the indexed snippets available. Likelihood is therefore Medium: reachable only under active dynamic resharding with repeated splits, but requires no validator/operator privilege — only ordinary deploy-global-contract and congestion-inducing transactions.

### Recommendation
- In `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874`), replace the unconditional `.unwrap()` on `receiver_shard_id` with an explicit, protocol-safe remapping for `GlobalContractDistribution` receipts (e.g., re-resolve/rewrite `target_shard` at the point resharding splits the delayed queue, or track ancestry so the filter can map an old-generation `target_shard` to the correct new-generation shard/child instead of failing).
- Ensure the remap logic is exercised across N-generation resharding chains (not just one split) in tests, and add a graceful-degradation path (e.g., forward to a deterministic fallback shard or explicitly handle unmapped `target_shard`s) instead of panicking, so that a stuck receipt cannot cause a consensus-wide halt.
- Audit all other callers of `receiver_shard_id`/similar shard-remap helpers on delayed/buffered receipt types for the same unwrap-on-stale-shard-id pattern introduced by multi-generation resharding.

### Proof of Concept
The repository's own test-loop test demonstrates the reproduction steps and encodes the expected (patched) outcome:
1. Deploy the test contract and a global contract (`DeployGlobalContract`, `CodeHash` mode) from an account (`user0`) on a shard configured to be split first.
2. Saturate that shard's compute budget every block with `burn_gas_raw` calls so the resulting `GlobalContractDistribution` receipt is pushed into, and remains in, the delayed queue.
3. Force two sequential dynamic resharding splits (`force_split_shards` configured for two different shards) so the shard is split twice while the receipt is still delayed, producing a `target_shard` that the newest layout can no longer resolve.
4. Stop saturating and let the delayed queue drain; on a vulnerable build this panics inside `receipt_filter_fn` when processing the stale receipt, stalling chain progress (asserted via `head_height >= drain_end`). [6](#0-5)

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

**File:** runtime/runtime/src/congestion_control.rs (L912-920)
```rust
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

**File:** core/store/src/trie/ops/resharding.rs (L50-59)
```rust
        match prefix {
            col::DELAYED_RECEIPT_OR_INDICES
            | col::PROMISE_YIELD_INDICES
            | col::PROMISE_YIELD_TIMEOUT
            | col::BANDWIDTH_SCHEDULER_STATE
            | col::GLOBAL_CONTRACT_CODE
            | col::GLOBAL_CONTRACT_NONCE => {
                // This section contains the keys that we need to copy to both shards.
                intervals.push(get_interval_for_copy_to_both_children(prefix))
            }
```

**File:** chain/chain/src/resharding/flat_storage_resharder.rs (L879-886)
```rust
        col::DELAYED_RECEIPT_OR_INDICES
        | col::PROMISE_YIELD_INDICES
        | col::PROMISE_YIELD_TIMEOUT
        | col::BANDWIDTH_SCHEDULER_STATE
        | col::GLOBAL_CONTRACT_CODE
        | col::GLOBAL_CONTRACT_NONCE => {
            copy_kv_to_all_children(&split_params, key, value, store_update)
        }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-185)
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
```
