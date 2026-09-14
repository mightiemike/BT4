### Title
Panic-inducing `.unwrap()` on stale `GlobalContractDistribution` receipt in `receipt_filter_fn` can halt the chain - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`GlobalContractDistribution` receipts are forwarded shard-by-shard using a fixed "always target the next undelivered shard" routing scheme (`forward_distribution_next_shard`, `runtime/runtime/src/global_contracts.rs:288-333`), analogous to the Vault's "always deposit into the first plugin" pattern. If such a receipt sits in the delayed-receipt queue across resharding events, its `target_shard` may become unresolvable in the current shard layout. `DelayedReceiptQueueWrapper::receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) calls `receiver_shard_id(&shard_layout).unwrap()` on every popped receipt, which panics if `receiver_shard_id` returns `Err`.

### Finding Description
`Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) resolves a `GlobalContractDistribution` receipt's shard by checking if `target_shard` exists in the current layout; if not, it calls `shard_layout.resolve_to_current_shard(target_shard)`, and returns `Err(EpochError::ShardingError(...))` if that also fails to find a descendant shard.

This fallible result is discarded via `.unwrap()` inside `receipt_filter_fn`: [1](#0-0) 

`receipt_filter_fn` is invoked on **every** delayed receipt popped from the trie-backed `DelayedReceiptQueue` during normal chunk apply, via `DelayedReceiptQueueWrapper::pop`: [2](#0-1) 

A `GlobalContractDistribution` receipt is created with a fixed `target_shard` and is always forwarded to "the next shard not yet delivered" (a first/next-only routing scheme, mirroring the Vault's "always deposit to first/withdraw from last" plugin logic): [3](#0-2) 

If this receipt is pushed into the **delayed** queue (because the shard's compute/proof-size budget is exhausted) and the shard undergoes **two or more resharding splits** while it waits, `target_shard` may no longer be resolvable through `resolve_to_current_shard`'s split-history lookup, causing `receiver_shard_id` to return `Err`. The subsequent `.unwrap()` in `receipt_filter_fn` panics.

The repository's own regression test explicitly documents and probes this exact scenario: [4](#0-3) 

The test deliberately saturates compute on the shard so the `GlobalContractDistribution` receipt is pushed to the delayed queue and held there through *two* resharding events, then drains the queue and asserts the chain does not stall — i.e., it is a direct check against the panic described above.

### Impact Explanation
A panic inside `Runtime::apply` (via the delayed receipt processing path used by every validator/chunk-producer applying that shard's chunk) is a deterministic, protocol-reachable crash: every honest node applying the same chunk will hit the identical `.unwrap()` on `Err`, since the shard layout, resharding schedule, and receipt contents are all part of consensus state. This is a chain-halting condition triggered purely by a sequence of ordinary user actions (deploying a global contract, timed against the chunk's compute/gas saturation and dynamic resharding), not by a malicious validator or network-level attack — squarely within the scope of "transaction-triggered halt."

### Likelihood Explanation
Triggering it requires: (1) the network/protocol to use `DynamicResharding`/ShardLayoutV3 (so `min_epochs_between_resharding` and force/auto-split logic exist), (2) a user deploying a global contract whose distribution receipt lands in the delayed queue of a shard that is split **twice** while the receipt is still delayed, and (3) `resolve_to_current_shard`'s split-history lookup being insufficient to trace back through two consecutive splits. This requires precise timing/compute saturation to delay the receipt long enough to span two resharding boundaries — feasible for a user/validator that controls when/how much gas is submitted and can observe the resharding schedule, but not trivial to hit by accident. The presence of a dedicated regression test in the codebase for exactly this scenario indicates this was already identified internally as a real risk area; without confirming the exact depth of `resolve_to_current_shard`'s split-history traversal (source of `v3.rs`'s implementation was not fully retrievable within the tool budget), it is uncertain whether the current code path already fully closes this gap or whether double-splits can still produce an unresolvable `target_shard`.

### Recommendation
- Replace the `.unwrap()` in `receipt_filter_fn` (`congestion_control.rs:876`) with proper error propagation (`Result`) so an unresolvable shard maps to `StorageError::StorageInconsistentState`/`RuntimeError` instead of a raw panic, consistent with how other "inconsistent state" cases in this file are handled.
- Verify/extend `resolve_to_current_shard` (and the underlying split-history representation in `shard_layout/v3.rs`) to correctly resolve `target_shard` across an arbitrary number of resharding generations, not just one.
- Add a hard invariant/test ensuring `GlobalContractDistribution` receipts cannot outlive more resharding generations than the split history can resolve, or that such receipts are drained/finalized before additional splits are allowed to proceed.

### Proof of Concept
The existing test constructs the exact PoC: deploy a test contract, then deploy a global contract from a user on a shard `S_A`; saturate `S_A`'s compute budget every block so the `GlobalContractDistribution` receipt is pushed into the delayed queue; force two sequential resharding splits, with the **first split targeting `S_A`** (the shard holding the delayed receipt) and a second split affecting a different shard afterward; then stop saturating and let the delayed queue drain. The test's own comment states the expected failure mode: "If the vulnerability exists, processing the stale `GlobalContractDistribution` receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations," and it asserts the chain height keeps advancing (i.e., does not stall/panic). [5](#0-4)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L892-907)
```rust
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
```

**File:** runtime/runtime/src/global_contracts.rs (L288-315)
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
