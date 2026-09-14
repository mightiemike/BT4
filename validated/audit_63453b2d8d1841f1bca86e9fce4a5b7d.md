## Analog Found: Stale global-contract distribution across a shard-layout migration ("resharding") — potential permanent loss of forwarded state

### Title
Global contract distribution receipts can become permanently unroutable/lost when a shard-layout migration (resharding) occurs mid-distribution - (File: `runtime/runtime/src/global_contracts.rs`, `runtime/runtime/src/congestion_control.rs`)

### Summary
The UMA finding describes a migration mechanism (`setMigrated`) that lets in-flight requests be forwarded to a new destination via `onlyRegisteredContract`, but leaves no way to actually resolve those forwarded requests once forwarding starts, because voting is disabled by `onlyIfNotMigrated`. The nearcore analog is the **global contract deployment/distribution** mechanism combined with **dynamic resharding** (shard-layout migration): a `DeployGlobalContractAction` submitted by any account starts a shard-by-shard "distribution" that is walked over multiple blocks by chaining `GlobalContractDistributionReceipt`s (`forward_distribution_next_shard`) using the shard set captured at initiation time. If the shard layout changes (a shard splits) while this walk is in flight, the receipt's `target_shard`/`already_delivered_shards` bookkeeping refers to shards from an old layout, and the runtime must remap them to the new layout via `receiver_shard_id()`/`get_parent_shard_id`, a remapping the codebase's own regression test says only works reliably for dynamic (`V3`) shard layouts with full split history.

### Finding Description
`action_deploy_global_contract` (`runtime/runtime/src/global_contracts.rs:25`) charges storage and calls `initiate_distribution`, which creates a `GlobalContractDistributionReceipt` with an auto-incrementing nonce and a `target_shard` set to the current shard [1](#0-0) . The distribution is then walked shard-by-shard: `forward_distribution_next_shard` computes `already_delivered_shards` and picks the "next" undelivered shard **using the shard layout at the current epoch** each time it forwards [2](#0-1) .

Separately, the delayed-receipt queue's `receipt_filter_fn` (used when popping receipts, including `GlobalContractDistribution` receipts, out of the delayed queue during resharding) must recompute `receiver_shard_id()` against the *current* shard layout to decide whether a receipt still belongs to the local shard [3](#0-2) . Both this filter and `get_outgoing_receipts_for_shard_from_store`'s resharding-reassignment path (`Self::reassign_outgoing_receipts_for_resharding`) depend on being able to trace a receipt's stale `target_shard` id back to a valid shard in the new layout via `get_parent_shard_id` [4](#0-3) .

The project's own regression test explicitly documents that this remap is fragile across **two or more** resharding generations, and that the mitigating fix only covers `V3`/dynamic shard layouts (which retain full split history), not the older static layouts: [5](#0-4) 

and the test's failure mode is stated directly: [6](#0-5) 

That is: after two resharding generations, a `GlobalContractDistributionReceipt` whose `target_shard` was set before the splits can fail to remap under `receiver_shard_id()`, which the test author expected could **panic** (halting the node/chain) or otherwise cause the distribution walk to stall. This is structurally identical to the UMA bug class: an in-flight "migration" artifact (a forwarded price request in UMA; a forwarded contract-distribution receipt here) is produced under one addressing scheme (the old `VotingV2`/old shard layout) and forwarded into a new addressing scheme (the migrated contract/new shard layout) without a guaranteed way to complete resolution, because the intermediate bookkeeping (`already_delivered_shards`, `target_shard`) is not migration-aware beyond one hop.

The bandwidth scheduler module explicitly acknowledges the same class of gap for resharding boundaries: [7](#0-6) , and the dynamic-resharding TODO list separately flags `runtime/runtime/src/congestion_control.rs:336` (parent shard's outgoing buffer cleanup after resharding) and `chain/chain/src/stateless_validation/state_witness.rs:260` (invalid witness proofs at resharding boundaries) as open issues.

### Impact Explanation
If a resharding event happens to overlap with an in-flight global-contract distribution across two layout transitions (a plausible scenario as `DynamicResharding` makes shard splits more frequent and shard-triggered by memory usage rather than a scheduled rare event), the distribution receipt can:
- fail to reach some shards, leaving those shards' accounts unable to `UseGlobalContractAction` a contract that other shards already have (state divergence in contract availability across shards), or
- trigger a panic in `receipt_filter_fn`/`receiver_shard_id()` while draining the delayed-receipt queue, which is a chunk-application code path — a panic there would crash/halt block production for that shard, a transaction-triggered halt.

Both are impact categories in scope (receipt loss and transaction-triggered halt).

### Likelihood Explanation
Dynamic resharding (splitting a shard when it grows past a memory threshold) makes back-to-back resharding events more likely over time than the old, rare, manually-scheduled reshardings; a global contract distribution walk (already multi-block by design, and now also gated behind the chunk's compute budget as shown by `test_deploy_global_contract_compute_cost_splits_chunks`, which demonstrates that distribution can be deferred across multiple blocks even without resharding) can span the time window in which two splits occur. The project's own test explicitly exists to catch this scenario, and states the fix "only works with V3 shard layouts," implying the underlying multi-generation remap problem is a real, previously-observed failure mode.

### Recommendation
- Make `GlobalContractDistributionReceipt` migration-safe across shard-layout changes for **all** shard-layout versions, not just `V3`/dynamic ones with full split history (or block reshardings from starting while other layout-agnostic receipt types with stale shard references, like global contract distribution, are still in flight).
- Ensure `receipt_filter_fn`/`receiver_shard_id()` and `get_parent_shard_id`-based remap paths fail closed with a recoverable re-route (e.g., re-broadcast to all current shards) rather than dropping/panicking when a stale `target_shard` cannot be traced through the split history.
- Add invariant checks/telemetry so an undeliverable or perpetually-stale distribution receipt is detected and retried rather than silently disappearing.

### Proof of Concept
The codebase's own regression test constructs the exact scenario and asserts the chain does not stall: [8](#0-7) 
It forces two sequential shard splits (`force_split_shards: vec![first_split_shard, second_split_shard]`) targeting the shard that originally received the `DeployGlobalContractAction`, then drains the delayed queue and checks that the head height keeps advancing — explicitly documenting that, absent the current fix, this exact transaction-triggered sequence ("deploy global contract" tx + two subsequent shard splits) was expected to panic in `receipt_filter_fn()`/stall the chain.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L143-171)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
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

**File:** runtime/runtime/src/congestion_control.rs (L868-911)
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

**File:** chain/chain/src/store/mod.rs (L405-428)
```rust
            }
            let receipts_shard_layout = epoch_manager.get_shard_layout(block_header.epoch_id())?;

            // get the shard from which the outgoing receipt were generated
            let receipts_shard_id = if shard_layout != receipts_shard_layout {
                shard_layout.get_parent_shard_id(shard_id)?
            } else {
                shard_id
            };

            let mut receipts = chain_store
                .get_outgoing_receipts(&receipts_block_hash, receipts_shard_id)
                .map(|v| v.to_vec())
                .unwrap_or_default();

            if shard_layout != receipts_shard_layout {
                // the shard layout has changed so we need to reassign the outgoing receipts
                Self::reassign_outgoing_receipts_for_resharding(
                    &mut receipts,
                    &shard_layout,
                    shard_id,
                    receipts_shard_id,
                )?;
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

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L123-134)
```rust
//! ## Resharding
//!
//! During resharding the list of existing shards changes. The only moment that is really
//! problematic for the bandwidth scheduler is the resharding boundary. At the boundary the shards
//! that send receipts will be from the old layout, while the receiving shards will be from the new
//! layout. At all other heights senders and receivers are from the same layout, so there are no
//! problems.
//! Ideally the bandwidth scheduler would make sure that bandwidth is properly granted when the sets
//! of senders and receivers are different, but this not implemented for now. The grants will be
//! slightly wrong (but still within limits) on the resharding boundary. The amount of work needed
//! to support scheduling at the boundary exceeds the benefits. For now reshardings happen very
//! rarely, so grants are very rarely wrong, although this might change in the future.
```
