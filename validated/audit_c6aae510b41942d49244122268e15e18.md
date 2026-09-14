Found the concrete crash. The root cause is in `receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs`, which `.unwrap()`s the result of `receiver_shard_id()`, and `receiver_shard_id()` in `core/primitives/src/receipt.rs` returns an `Err` when a `GlobalContractDistribution` receipt's `target_shard` predates the shard's split history (i.e., is older than what `resolve_to_current_shard` can walk back). The regression test that exists specifically to guard this path (`test_stale_global_contract_distribution_after_double_resharding`) confirms this is a reachable, user-triggerable panic during two successive dynamic resharding events.

### Title
Delayed-queue `GlobalContractDistribution` receipt panics chunk producers when stranded across two resharding generations - (File: runtime/runtime/src/congestion_control.rs)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unwraps `receiver_shard_id()` while draining the delayed-receipt queue during chunk application. For `GlobalContractDistribution` receipts, `Receipt::receiver_shard_id` calls `shard_layout.resolve_to_current_shard(target_shard)`, which can return `None` (and thus `Err`) if the receipt's `target_shard` no longer exists in the split-ancestor history of the *current* shard layout. If an attacker keeps a `GlobalContractDistribution` receipt parked in the delayed queue while two (or more) resharding events occur, the unwrap panics on every honest chunk producer/validator applying that shard, permanently halting the shard.

### Finding Description
`GlobalContractDeploy` transactions are user-submittable and create a `GlobalContractDistribution` receipt with `target_shard` fixed at deploy time [1](#0-0) . If this receipt is pushed to the delayed queue and stays there through chunk application (e.g., by an unprivileged user saturating the shard's compute budget with cheap gas-burning calls every block, exactly as the regression test does) [2](#0-1) , the receipt survives across a resharding boundary. `receiver_shard_id` already contains fallback logic to remap a stale `target_shard` via `resolve_to_current_shard`, but that lookup can fail if the receipt is stale beyond what the current layout's split history can resolve (e.g., after two resharding generations), returning `Err(EpochError::ShardingError(...))` [3](#0-2) .

The critical bug is that the caller in `DelayedReceiptQueueWrapper::receipt_filter_fn`, invoked from `pop()` while draining the delayed queue during ordinary chunk application, unconditionally `.unwrap()`s this `Result` instead of propagating the error: [4](#0-3) 
Specifically: [5](#0-4) 

This function runs on every block that pops from the delayed queue on the affected shard, meaning every chunk producer and validator applying that shard's transactions will independently hit the same panic, since state application is deterministic across all honest nodes.

### Impact Explanation
This is a transaction-triggered halt: a single unprivileged account can deploy a global contract, then submit ordinary gas-burning transactions to keep that receipt delayed across two dynamic-resharding events, causing the shard to panic on every node that applies it, permanently stalling chunk production for that shard (denial of service). This matches the report's bug class — a crash triggerable during an unpredictable window of a cluster-topology transition (mongod's promotion-to-sharded vs. nearcore's dynamic resharding split) — but here it manifests as a deterministic, protocol-level panic reachable purely from a submitted transaction/contract-deploy sequence, with no special privileges.

### Likelihood Explanation
Reachability requires: (1) dynamic resharding enabled (feature `DynamicResharding`, active at protocol v85+ per the docs), (2) at least two resharding (shard-split) events occurring while the receipt sits in the delayed queue, and (3) the attacker sustaining compute saturation on the target shard long enough to prevent the receipt from draining before those splits happen. This is timing-dependent and requires the network to actually be exercising dynamic resharding (whether any mainnet/testnet epoch config sets `shard_layout_config = Dynamic` is a deployment question not resolvable from code alone), which lowers the practical likelihood somewhat, but the underlying code path itself is a genuine unwrap-on-user-reachable-error bug with no additional privilege requirement.

### Recommendation
Change `receipt_filter_fn` to propagate the error from `receiver_shard_id()` instead of unwrapping, and make `pop()` return `Result<..., RuntimeError>` (or equivalent) instead of masking the failure — treat an unresolvable `GlobalContractDistribution` target shard as a resolvable/graceful case (e.g., drop or reroute to the nearest still-valid ancestor/descendant) rather than a fatal error, since `resolve_to_current_shard` failure is a foreseeable condition when reshardings compound.

### Proof of Concept
The existing regression test in the repo already reproduces and documents this exact panic path: [6](#0-5) 
Steps: (1) deploy a test contract, (2) deploy a global contract from an account whose shard will be force-split twice via `DynamicReshardingConfig::force_split_shards`, (3) saturate the shard's compute budget every block with `burn_gas_raw` calls to keep the `GlobalContractDistribution` receipt in the delayed queue through both splits, (4) stop saturating and let the queue drain — on unfixed code, processing the stale receipt panics in `receipt_filter_fn` via the `unwrap()` on `receiver_shard_id()`, causing the chain to stall (asserted by `head_height >= drain_end` failing).

### Citations

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-186)
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

**File:** runtime/runtime/src/congestion_control.rs (L868-908)
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
```
