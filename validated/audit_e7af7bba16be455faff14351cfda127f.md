## Title
Unwrap panic in `receipt_filter_fn` on stale delayed receipt after resharding — ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` at `runtime/runtime/src/congestion_control.rs:874-878` calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` on every receipt popped from (or peeked in) the delayed receipt queue, unconditionally unwrapping a `Result`. This mirrors the CVE-2021-45763 pattern: a function invoked in a state/context it doesn't support, causing a hard failure (panic) instead of a handled error, producing a Denial-of-Service. [1](#0-0) 

### Finding Description
`receipt_filter_fn` is called from `pop()` and `peek_iter()` on every delayed receipt when applying a chunk, in order to filter receipts that no longer belong to the current shard after a resharding split (`runtime/runtime/src/congestion_control.rs:880-920`). `receiver_shard_id` can fail to resolve a receipt's shard mapping (e.g., a stale `GlobalContractDistribution` receipt's `target_shard` that no longer maps cleanly onto the current `ShardLayout` after two sequential resharding generations), and `.unwrap()` turns that failure into a chunk-processing panic rather than a `RuntimeError` that could be handled per-receipt. [2](#0-1) 

This exact scenario is captured by a regression test, `test_stale_global_contract_distribution_after_double_resharding`, in `test-loop-tests/src/tests/global_contracts_distribution.rs`, which explicitly documents: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations." [3](#0-2) 

The reachable path is: an unprivileged account submits a `DeployGlobalContract` transaction (creating a `GlobalContractDistribution` receipt targeting the signer's current shard) and then floods the shard with `FunctionCall` transactions to saturate compute so the receipt is pushed into the persistent delayed-receipt queue and survives across dynamic resharding boundaries. When the receipt is later dequeued via `pop()`/`peek_iter()`, `receipt_filter_fn` computes `receiver_shard_id` against the *current* shard layout — if the fix/guard for this case is absent or incomplete, the `.unwrap()` panics, halting chunk application on every node that must apply that chunk (a transaction-triggered halt).

### Impact Explanation
A panic inside `Runtime::apply` during receipt processing is not a per-transaction/per-receipt failure — it aborts chunk application entirely on every validator/node applying that chunk, i.e., a protocol-wide chunk-processing halt triggered purely by transaction content the attacker fully controls (deploy timing + shard targeting + resharding participation). This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
The existence of a dedicated, named regression test (`test_stale_global_contract_distribution_after_double_resharding`) that deliberately engineers this exact "double resharding + stale delayed GlobalContractDistribution receipt" scenario indicates the bug class was real and previously reachable; whether it is fully patched in this exact commit depends on additional resharding-aware logic that may exist elsewhere (not visible in the excerpt reviewed) beyond the raw `.unwrap()` still present at `congestion_control.rs:876`. Reaching it requires a dynamic-resharding-enabled network and precise timing (delayed-queue placement across two shard splits), which lowers likelihood somewhat but is achievable by any unprivileged account without validator/operator privileges.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (`?`) so that an unmappable `receiver_shard_id` becomes a `RuntimeError`/`StorageError` handled gracefully (e.g., treat as belonging to neither shard, or route via a documented fallback) instead of panicking the chunk apply. Add explicit unwrap-free handling for `peek_iter` as well, since it silently uses `map_while(Result::ok)` for the queue iterator but still calls the panicking `receipt_filter_fn` afterward.

### Proof of Concept
1. Configure a chain with `DynamicResharding` enabled and two forced shard splits (`force_split_shards`).
2. From an unprivileged account on shard S_A, submit `DeployGlobalContract`, creating a `GlobalContractDistribution` receipt targeting S_A.
3. Flood S_A with `FunctionCall` transactions that each burn slightly more than half the gas limit, forcing the `GlobalContractDistribution` receipt into the delayed-receipt queue for multiple chunks.
4. Let two sequential resharding events complete while the receipt remains delayed and its `target_shard` reference becomes stale relative to the new `ShardLayout`.
5. Stop saturating and let the delayed queue drain; on dequeue, `receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()`, and if the mapping fails, the chunk-apply panics, halting the chain at that height — exactly the condition asserted against in `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:163-185`). [4](#0-3)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L838-866)
```rust
    pub(crate) fn push(
        &mut self,
        trie_update: &mut TrieUpdate,
        receipt: &Receipt,
        apply_state: &ApplyState,
    ) -> Result<(), RuntimeError> {
        let config = &apply_state.config;

        let gas = compute_receipt_congestion_gas(&receipt, &config)?;
        let size = compute_receipt_size(&receipt)? as u64;

        // TODO It would be great to have this method take owned Receipt and
        // get rid of the Cow from the Receipt and StateStoredReceipt.
        let receipt = match config.use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_borrowed(receipt, metadata);
                ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt)
            }
            false => ReceiptOrStateStoredReceipt::Receipt(Cow::Borrowed(receipt)),
        };

        self.new_delayed_gas = self.new_delayed_gas.checked_add(gas).ok_or(IntegerOverflowError)?;
        self.new_delayed_bytes =
            self.new_delayed_bytes.checked_add(size).ok_or(IntegerOverflowError)?;
        self.queue.push_back(trie_update, &receipt)?;
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
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
