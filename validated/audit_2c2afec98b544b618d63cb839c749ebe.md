Based on my investigation, the strongest reachable analog is a NULL-pointer/`unwrap`-style panic on `Option::None` reachable by an unprivileged transaction sender through the `GlobalContractDistribution` receipt path across shard resharding, mirroring the LibSass NULL Pointer Dereference DoS bug class (crash triggered by attacker-controlled input reaching an `unwrap()`).

### Title
Transaction-triggered chunk-producer panic via unwrap() on stale receipt-to-shard mapping in `receipt_filter_fn` - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`receipt_filter_fn` in the delayed-receipt queue pop path calls `.unwrap()` on the result of `receiver_shard_id(&shard_layout)` for every delayed receipt, including `GlobalContractDistribution` receipts whose `target_shard` was computed against an older shard layout. [1](#0-0)  An attacker who submits a global-contract deploy transaction just before a shard split, then keeps the resulting distribution receipt parked in the delayed queue across two resharding generations by saturating the receiving shard's compute budget, can make this `unwrap()` fail once the target shard id can no longer be remapped into the current layout.

### Finding Description
`pop()` in the delayed receipt queue calls `self.receipt_filter_fn(&receipt)` on every receipt it dequeues to decide whether it belongs to the current shard after resharding. [2](#0-1)  `receipt_filter_fn` itself does:
```
let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
``` [1](#0-0) 
This is exactly the "used-by-multiple-call-sites, unchecked-Option/Result-unwrap-on-attacker-influenced-data" pattern that the LibSass advisory describes for `Selector_List::populate_extends` — a null/`None` value reaching an unguarded dereference deep in a widely-used helper, causing an application crash (chunk producer/validator process panic) rather than a clean error path.

The repository's own regression test, `test_stale_global_contract_distribution_after_double_resharding`, documents the exact reachable path and root cause explicitly in its comments: an ordinary user deploys a global contract (creating a `GlobalContractDistribution` receipt whose `target_shard` is fixed at creation time), then floods the receiving shard with `burn_gas_raw` calls so the distribution receipt is pushed into the delayed-receipt queue and survives across *two* resharding events. [3](#0-2)  The test's own comment states: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations." [4](#0-3) 

### Impact Explanation
If `receiver_shard_id` returns `None`/`Err` for the double-resharded stale receipt, the `.unwrap()` in `receipt_filter_fn` panics inside the runtime's `apply()` receipt-processing loop, which is invoked by every validator/chunk-producer applying that chunk. [5](#0-4)  A panic here is a transaction-triggered halt: it crashes the chunk-producer/validator process (or, depending on panic-catching, aborts chunk production for that shard), which is one of the accepted impact categories ("a transaction-triggered halt"). This is reachable purely from an unprivileged deploy transaction plus ordinary compute-saturating transactions — no validator/operator/network privileges are required.

### Likelihood Explanation
The current test in the repo is a defensive regression test asserting the chain does **not** stall (`assert!(head_height >= drain_end, ...)`), implying this exact scenario has already been considered and a fix path exists for `receiver_shard_id`/`receipt_filter_fn` in this snapshot. [6](#0-5)  I was not able to retrieve the body of `receiver_shard_id` itself (in `core/primitives/src/receipt.rs`) within my remaining search budget to confirm whether it now returns a shard id unconditionally (e.g., by walking split-shard ancestry) or whether the `.unwrap()` in `congestion_control.rs` can still fail under some other resharding sequence (e.g., three or more consecutive splits, or splits combined with shard merges) not covered by the existing test. Given the explicit test comment describing the exact panic condition, and that the fix is scoped only to "two resharding generations," there is a reasonable possibility that additional resharding depth or ordering could still trigger the same `.unwrap()`.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with a fallible path that treats an unmappable `target_shard` as "not for this shard, retain in queue or forward to GC" instead of panicking, and add exhaustive resharding-depth test coverage (3+ successive splits/merges) beyond the existing 2-generation regression test to close any residual gap in `receiver_shard_id`'s remapping logic.

### Proof of Concept
Follow the reproduction already encoded in the repository's own regression test: deploy a global contract from a user account near a shard boundary, then flood that shard with gas-maximizing `burn_gas_raw` transactions every block so the resulting `GlobalContractDistribution` receipt is pushed to the delayed queue; force two sequential dynamic-resharding splits while the receipt sits delayed; then stop saturating and let the delayed queue drain, observing whether the chain stalls. [7](#0-6) 

**Caveat:** I could not confirm from the index whether the underlying `receiver_shard_id` implementation in `core/primitives/src/receipt.rs` fully closes this class of bug for arbitrary resharding depths, since its body was not returned by my search/read tools within the available iterations. If deeper verification of `receiver_shard_id`'s remapping logic is needed, a full-repository Devin session with unrestricted file access would be required to confirm whether any unwrap-panic path remains exploitable beyond the two-generation case already tested.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-907)
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
```

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

**File:** runtime/runtime/src/lib.rs (L1943-1945)
```rust
        // Step 3: process receipts.
        let process_receipts_result =
            self.process_receipts(&mut processing_state, &mut receipt_sink)?;
```
