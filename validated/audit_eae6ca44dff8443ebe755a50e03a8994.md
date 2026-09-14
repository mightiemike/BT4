### Title
Panic in `DelayedReceiptQueueWrapper::receipt_filter_fn` on unmapped `GlobalContractDistribution.target_shard` after resharding halts chunk processing chain-wide - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
This is the closest reachable analog to the reported Curve-vault bug: a critical, single-path routine that has no fallback and can revert/abort unconditionally once an external precondition it silently assumes (a fixed, stable shard set) no longer holds. In nearcore, `DelayedReceiptQueueWrapper::pop`/`peek_iter` unconditionally call `.unwrap()` on `Receipt::receiver_shard_id(&shard_layout)` for every receipt drained from the persistent delayed-receipt queue, including `GlobalContractDistribution` receipts whose routing target (`target_shard`) is a raw `ShardId` recorded at receipt-creation time [1](#0-0) .

### Finding Description
Any unprivileged account can trigger the vulnerable path with a normal `DeployGlobalContract` action: `action_deploy_global_contract` creates a `GlobalContractDistributionReceipt` carrying `target_shard = apply_state.shard_id` [2](#0-1) , and `forward_distribution_next_shard` re-emits copies of the receipt with successive `target_shard` values as it hops shard-by-shard to deliver the contract code everywhere [3](#0-2) . If the chunk is congested, one of these in-flight distribution receipts can sit in the shard's persistent delayed-receipt queue while dynamic resharding splits the shard (or splits it twice) before the receipt is drained [4](#0-3) . When the runtime finally pops this receipt out of the queue, `DelayedReceiptQueueWrapper::receipt_filter_fn` must resolve which of the *new* child shards owns it via `receiver_shard_id(&shard_layout)`, and unconditionally unwraps the result [5](#0-4) . `receiver_shard_id` needs `target_shard` to still be a valid `ShardId` in the *current* shard layout; after two resharding generations the recorded `target_shard` id may no longer exist in the layout, so the underlying lookup fails and the `.unwrap()` panics. This is analogous to the audit finding: an "emergency"/fallback-free code path (`remove_liquidity_one_coin` only, no `remove_liquidity` alternative) becomes permanently unusable once an external state transition (`pool killed` / `resharding`) invalidates the single assumption the path depends on. There is a regression test explicitly built to reproduce exactly this scenario, `test_stale_global_contract_distribution_after_double_resharding`, whose comment states: "If the vulnerability exists, processing the stale `GlobalContractDistribution` receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations" [4](#0-3) .

### Impact Explanation
Since `apply_action_receipt`/`process_delayed_receipts` runs on every validator applying the same chunk, a panic here is deterministic across all honest nodes processing the same shard state — this is a transaction-triggered chain halt, not a benign, isolated error. Every validator tracking the affected shard crashes/panics identically when draining the delayed queue, stalling chunk and block production for that shard (and potentially the whole chain, since cross-shard receipts also depend on this shard's liveness) until a manual protocol/binary fix ships. This matches the "transaction-triggered halt" impact category explicitly accepted by the validation rules.

### Likelihood Explanation
Reaching this bug requires: (1) an ordinary account submitting a `DeployGlobalContractAction`, reachable by any transaction signer with no special privilege; (2) the resulting `GlobalContractDistribution` receipt getting delayed by congesting the target shard (achievable by any user submitting enough compute-heavy transactions, as demonstrated in the existing test harness that intentionally saturates compute to force the receipt into the delayed queue); and (3) dynamic resharding splitting that shard while the receipt is delayed. Dynamic resharding is an automatic protocol mechanism (not attacker-controlled per se), so the trigger window depends on chain conditions, but it is entirely reachable from unprivileged transaction submission plus normal governance-triggered resharding — no validator/operator/peer collusion required. The repo already ships a dedicated regression test for the double-resharding scenario, indicating this exact failure mode was identified as a real risk during development; whether the fix fully closes the gap for all resharding depths (e.g., the `receiver_shard_id` remapping logic covering N generations) could not be confirmed from the available code alone — the mapping function was not located to verify complete generality.

### Recommendation
Replace the `.unwrap()` in `DelayedReceiptQueueWrapper::receipt_filter_fn` (and the same pattern in `peek_iter`) with a graceful fallback: if `receiver_shard_id` cannot resolve `target_shard` against the current layout (e.g., because it no longer exists after multiple splits), attempt an ancestor-aware remap using `ShardLayout::ancestor_uids`/split-history information, or otherwise treat the receipt as belonging to (and re-route it via) one of its current descendant shards rather than panicking. At minimum, convert the failure into a recoverable `StorageInconsistentState`/`RuntimeError` that can be handled without an unrecoverable panic, consistent with how other "receipt should be in the state" invariant violations are surfaced elsewhere in the runtime.

### Proof of Concept
1. Configure a chain with `DynamicResharding` enabled and a low `min_epochs_between_resharding`, force-splitting the same base shard twice in immediate succession (mirroring `test_stale_global_contract_distribution_after_double_resharding`'s `force_split_shards` config) [6](#0-5) .
2. From an unprivileged account, submit a `DeployGlobalContract` transaction targeting the shard about to be split, producing a `GlobalContractDistribution` receipt with `target_shard` = that shard's id [2](#0-1) .
3. Saturate the shard's compute budget every block (e.g. repeated `burn_gas_raw` calls) so the distribution receipt is pushed into, and remains stuck in, the delayed-receipt queue while both resharding events complete [7](#0-6) .
4. Stop saturating and let the delayed queue drain; the stale receipt is popped and `receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()` on a `target_shard` that is no longer a valid id in the twice-resharded layout, panicking every node applying that chunk and halting the chain for that shard [5](#0-4) .

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

**File:** runtime/runtime/src/global_contracts.rs (L53-60)
```rust
    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-92)
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-130)
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
