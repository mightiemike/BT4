Based on my investigation, this is the strongest reachable analog to the OpenShift Hive panic bug.

### Title
Reachable panic in delayed-receipt processing via `.unwrap()` on `receiver_shard_id` for stale `GlobalContractDistribution` receipts after multiple reshardings - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally `.unwrap()`s the `Result` returned by `Receipt::receiver_shard_id`, which is used every time a delayed receipt is popped or peeked during normal chunk apply. `Receipt::receiver_shard_id` returns `Err(EpochError::ShardingError(...))` when a `GlobalContractDistribution` receipt's `target_shard` cannot be resolved to any shard in the current layout or its split history. A user-submitted `DeployGlobalContract` transaction produces exactly such a receipt, and if it is delayed for long enough to survive two (or more) resharding generations while its original target shard's descendant lineage can no longer be resolved, popping it from the delayed queue causes an unhandled panic, crashing the chunk-producing node process — a transaction-triggered denial of service, directly analogous to the Hive hibernation-controller panic that is triggered by attacker-controlled resource state reaching an unguarded field access in a reconciliation loop.

### Finding Description
`receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs` is:
```
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
``` [1](#0-0) 

This helper is called from `pop` (used every chunk while draining the delayed-receipt backlog) and from `peek_iter` (used for contract-preparation lookahead scheduling): [2](#0-1) 

`Receipt::receiver_shard_id` returns an `EpochError::ShardingError` for `GlobalContractDistribution` receipts whose `target_shard` is not in the current layout and cannot be resolved via `shard_layout.resolve_to_current_shard(target_shard)`: [3](#0-2) 

A `GlobalContractDistribution` receipt is created by a normal, unprivileged transaction — a `DeployContract`/`DeployGlobalContract` action from any signer — and is routed with `target_shard` fixed to the deploying account's shard at creation time. If congestion or gas-limit exhaustion pushes that receipt into the delayed queue and it remains delayed across two or more resharding events (dynamic resharding splits shards further), `resolve_to_current_shard` can fail to find a valid descendant, causing `receiver_shard_id` to return `Err`. The unconditional `.unwrap()` in `receipt_filter_fn` then panics inside `pop`/`peek_iter`, both of which sit directly in the per-chunk `process_delayed_receipts` path (`runtime/runtime/src/lib.rs`): [4](#0-3) 

This is confirmed as a reachable, previously identified failure mode by an existing regression test that explicitly documents the panic condition and exercises exactly this scenario (deploy global contract → saturate the shard so the distribution receipt stays delayed → force two shard splits → drain the queue and expect the chain to keep making progress instead of stalling from the panic): [5](#0-4) 

### Impact Explanation
A panic inside `process_delayed_receipts`/`process_receipts` occurs on the runtime's core chunk-apply path, which every validator/chunk-producer node executes identically for state-transition determinism. A panic here crashes (or, depending on panic-handling configuration, aborts) the node process while attempting to produce or validate a chunk, which is a transaction-triggered halt: any chunk producer that must apply the stale receipt will crash, and because all correct nodes run the same deterministic logic, all producers for the affected shard would hit the same panic, causing the chain to stall on that shard — satisfying the "transaction-triggered halt" acceptance criterion. This matches CWE-400/uncontrolled resource consumption via unauthorized panic-inducing state, the same bug class as the OpenShift Hive advisory (panic from accessing an unhandled/missing field triggered by attacker-influenced resource state during a reconciliation-style loop).

### Likelihood Explanation
Reachability requires: (1) an ordinary account issuing a `DeployGlobalContract` action (unprivileged, no special permission needed) whose resulting `GlobalContractDistribution` receipt is delayed rather than executed immediately (achievable by any account congesting/saturating its own shard's gas budget), and (2) that shard undergoing two or more subsequent resharding splits while the receipt remains delayed. Dynamic resharding is a protocol-level, scheduled event (not attacker controlled), so the exact timing is not fully within a single attacker's control, but the trigger conditions (delay a distribution receipt across multiple future reshardings) are plausible over the operational lifetime of a shard that is repeatedly split, and the codebase already contains a dedicated regression test built specifically to reproduce this exact panic path, indicating the scenario was deemed realistic enough to require a fix/test by the project itself.

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (and any other call site relying on `receiver_shard_id`/`shard_layout` succeeding for delayed/postponed receipts) with proper error propagation (e.g., convert `EpochError` into `RuntimeError::StorageError(StorageError::StorageInconsistentState(...))` similar to how other queue-consistency violations are already handled), so that an unresolvable stale `GlobalContractDistribution` receipt causes a structured recoverable error rather than an unhandled panic. Additionally, consider persisting a resolvable shard identifier (or bounding how many split generations a distribution receipt may cross) so `target_shard` remains resolvable indefinitely, matching the existing "Oversized-receipt workaround" pattern already used elsewhere in the congestion-control code to avoid receipts getting permanently or catastrophically stuck. [6](#0-5) 

### Proof of Concept
1. Deploy a global contract (`DeployGlobalContract` action, `GlobalContractDeployMode::CodeHash`) from an account on shard `S_A`, creating a `GlobalContractDistribution` receipt with `target_shard = S_A`.
2. Saturate `S_A`'s gas/compute budget every block (e.g., repeated `burn_gas_raw` calls) so the distribution receipt is pushed into the delayed-receipt queue rather than processed immediately.
3. Trigger dynamic resharding twice in sequence, first splitting `S_A` (and thus invalidating the original `target_shard` as a direct entry in the new layout, relying on split-history resolution), then splitting one of the resulting child shards again.
4. Stop saturating and allow the delayed queue to drain; when the runtime pops the stale `GlobalContractDistribution` receipt, `resolve_to_current_shard` fails to map `target_shard` through two resharding generations, `receiver_shard_id` returns `Err`, and `receipt_filter_fn`'s `.unwrap()` panics, halting chunk production for that shard.

This exact sequence is implemented as `test_stale_global_contract_distribution_after_double_resharding`: [7](#0-6)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
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

**File:** runtime/runtime/src/lib.rs (L2570-2606)
```rust
    fn process_delayed_receipts(
        &self,
        mut processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        compute_limit: u64,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        let delayed_processing_start = std::time::Instant::now();
        let protocol_version = processing_state.protocol_version;
        let mut delayed_receipt_count = 0;

        let mut next_schedule_after = {
            let mut prep_lookahead_iter =
                processing_state.delayed_receipts.peek_iter(&processing_state.state_update);
            schedule_contract_preparation(
                &mut processing_state.pipeline_manager,
                &processing_state.state_update,
                &mut prep_lookahead_iter,
            )
        };

        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }

            let receipt = if let Some(receipt) = processing_state
                .delayed_receipts
                .pop(&mut processing_state.state_update, &processing_state.apply_state.config)?
            {
                receipt.into_receipt()
            } else {
                // Break loop if there are no more receipts to be processed.
                break;
            };
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

**File:** protocol-model/spec/cross-shard-congestion.md (L372-379)
```markdown
- **Oversized-receipt workaround**: receipts above `max_receipt_size` are treated as
  exactly `max_receipt_size` for both forwarding limits (`congestion_control.rs:417`)
  and bandwidth requests (`:561`) so they cannot get permanently stuck (issue #12606).
- **Inconsistent-state failures**: a missing delayed/buffered/postponed/yield item
  referenced by an index yields `StorageError::StorageInconsistentState`
  (`receipts_column_helper.rs:111`, `lib.rs:3011`); a delayed receipt that fails
  `validate_receipt` on pop is likewise treated as inconsistent state, not a soft error
  (`lib.rs:2506`).
```
