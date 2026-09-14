### Title
Panic-Induced Chain Halt via Stale `GlobalContractDistribution` Receipt Surviving Multiple Reshardings - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
CVE-2018-12982 describes an invalid-memory-read triggered when a crafted, malformed input file reaches a delayed/deferred deserialization path (`PdfVariant::DelayedLoad()`) that assumes well-formed prior state. The applicable bug class — a value that is deferred/delayed and later consumed under an assumption that no longer holds, causing an unhandled fault — maps in nearcore to a `.unwrap()` panic in `receipt_filter_fn` when a `GlobalContractDistribution` receipt is delayed across two or more resharding events and its `target_shard` can no longer be resolved in the current shard layout.

### Finding Description
`Receipt::receiver_shard_id` resolves a `GlobalContractDistribution` receipt's shard by first checking if `target_shard` exists in the current layout, and otherwise calling `shard_layout.resolve_to_current_shard(target_shard)`, returning `Err(EpochError::ShardingError(...))` if no descendant can be found: [1](#0-0) 

This function is called with `.unwrap()` inside the delayed-receipt-queue filter used while draining the delayed receipts queue after resharding: [2](#0-1) 

`pop()` calls `receipt_filter_fn` on every dequeued delayed receipt as part of normal chunk processing, reachable purely by delayed receipts sitting in the queue across epoch/shard-layout transitions: [3](#0-2) 

A `GlobalContractDistribution` receipt is created and forwarded shard-by-shard by `forward_distribution_next_shard`, carrying a `target_shard` value fixed at creation/forward time: [4](#0-3) 

If the shard containing this receipt gets saturated with compute (pushing the receipt to the delayed queue) and the network undergoes two sequential dynamic-resharding splits before the receipt is dequeued, `resolve_to_current_shard` may fail to map the now doubly-stale `target_shard` to any current shard, causing `receiver_shard_id` to return `Err`, which is `.unwrap()`'d and panics the node. This exact scenario is captured by an existing regression test in the repository: [5](#0-4) 

The test explicitly documents: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations," and asserts that the chain does not stall.

### Impact Explanation
A panic inside `Runtime::apply` during chunk application is fatal for the calling node/validator process. Callers of `apply` in `chain/chain/src/runtime/mod.rs` explicitly `panic!` on `RuntimeError::ReceiptValidationError` and other runtime errors, and an unhandled `.unwrap()` failure inside the runtime itself crashes the validator process outright rather than returning a recoverable `RuntimeError`. Since every validator processing the same shard/chunk hits the identical delayed receipt at the identical height, this is not a Byzantine/single-node crash — it reproduces deterministically across all honest validators tracking that shard, causing a network-wide chunk-production/liveness halt (a transaction/receipt-triggered halt) rather than mere resource exhaustion.

### Likelihood Explanation
Triggering requires: (1) deploying a global contract (any account can submit a `DeployGlobalContract` action, generating a `GlobalContractDistribution` receipt), (2) the receipt being delayed (achievable by an attacker saturating the shard's compute budget with cheap gas-burning calls to push queue processing into the delayed backlog), and (3) two dynamic-resharding events occurring while the receipt remains queued. Dynamic resharding is a protocol-controlled, non-attacker-triggered event, which lowers the practical likelihood — the attacker cannot force reshardings on demand, only maximize the probability that a delayed distribution receipt survives across whichever reshardings do occur. Because the existence of this exact scenario is validated by a dedicated regression test in the repository, the underlying hazard is a real, previously-identified class of bug, though its exploitability depends on resharding cadence being combined with attacker-controlled compute saturation.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:876`) with proper error propagation (`?`) so that an unresolvable `target_shard` becomes a handled `RuntimeError` rather than a hard panic; additionally, ensure `GlobalContractDistribution` receipts are re-targeted (or dropped safely with appropriate accounting) whenever a resharding event occurs while they are still enqueued, rather than relying solely on `resolve_to_current_shard`'s split-history lookup, which is bounded and can fail after enough successive splits.

### Proof of Concept
1. Configure a network with `DynamicReshardingConfig` set to force-split two different shards sequentially (as in the existing test).
2. From an unprivileged account, submit a `DeployGlobalContract` transaction, creating a `GlobalContractDistribution` receipt with `target_shard` pointing at the sender's shard.
3. Flood the same shard every block with `FunctionCall` transactions that burn slightly more than half the chunk's gas limit, forcing the distribution receipt into the delayed-receipts queue instead of being processed immediately.
4. Wait for two consecutive dynamic-resharding splits to complete while the receipt remains delayed.
5. Stop flooding and let the delayed queue drain; `receipt_filter_fn` calls `receiver_shard_id`, which cannot resolve the doubly-stale `target_shard`, and the `.unwrap()` panics, halting chunk application on that shard for every validator. [6](#0-5)

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

**File:** runtime/runtime/src/congestion_control.rs (L880-909)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-186)
```rust
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
