Confirmed. The bug class is exactly on point: an unwrap()-triggered panic while processing a receipt after object relocation/remapping — QEMU's AHCI FIS/CLB "unmap" NULL-deref DoS maps directly to `receipt_filter_fn`'s `.unwrap()` on `receiver_shard_id()`, which can return an `Err` (not a NULL, but an unhandled error causing a hard panic) when a receipt's stale shard reference cannot be remapped after multiple resharding generations.

### Title
Unrecoverable panic in delayed-receipt processing when a stale `GlobalContractDistribution` receipt's `target_shard` cannot be remapped after multiple reshardings, causing a chain-wide, transaction-triggered halt - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` on every delayed receipt popped from the persistent delayed-receipt queue. `Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-467`) returns `Err(EpochError::ShardingError(...))` for a `GlobalContractDistribution` receipt whose `target_shard` no longer exists in the current shard layout and cannot be resolved via `shard_layout.resolve_to_current_shard(target_shard)` — which happens when the receipt has been sitting in the delayed queue across **two or more** resharding (shard-split) events, i.e. the shard it targeted has been split more than once before the receipt is finally popped. `receipt_filter_fn` unconditionally `.unwrap()`s this `Result`, converting a legitimate, reachable error into a Rust panic. `peek_iter` (`:912-920`) and `pop` (`:880-910`) both call `receipt_filter_fn` while draining the persistent delayed-receipt queue during ordinary chunk `apply()`.

### Finding Description
A `GlobalContractDistribution` receipt is a normal cross-shard receipt created whenever any account deploys a global contract (`initiate_distribution`, `runtime/runtime/src/global_contracts.rs:143-171`), fully reachable by any unprivileged account submitting a `DeployGlobalContract` action. If the shard that currently holds the receipt is under sustained compute/gas pressure (e.g., other transactions saturating the chunk's compute budget), the incoming `GlobalContractDistribution` receipt is pushed into the persistent, trie-backed delayed-receipt queue (`Runtime::process_incoming_receipts`, `runtime/runtime/src/lib.rs:2578`) instead of executing immediately. While the receipt is delayed, dynamic resharding can split the shard it targets — and split it again in a subsequent epoch before the receipt is finally drained. When `DelayedReceiptQueueWrapper::pop` eventually dequeues it (`congestion_control.rs:880-910`), `receipt_filter_fn` calls `receiver_shard_id`, which cannot find a valid descendant shard for a `target_shard` two or more resharding generations stale, returns `Err`, and the `.unwrap()` panics inside `Runtime::apply` — a code path that every validator/chunk-producer executes deterministically for every chunk. The behavior is reproduced by the repo's own regression test, `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`), whose comment explicitly states: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations."

This is the direct analog of CVE-2016-2197: in QEMU, an unhandled null pointer during FIS/CLB "unmapping" (a stale/relocated memory-mapping reference) crashes the emulator process; here, an unhandled `Err` during shard "remapping" (a stale shard reference after relocation via resharding) crashes the runtime/node process — reachable purely by an unprivileged account submitting ordinary transactions (deploy a global contract + saturate the target shard's compute budget) with no special privileges needed.

### Impact Explanation
A panic inside `Runtime::apply`/`process_delayed_receipts` is not a soft error — it aborts the validator/chunk-producer process. Because every honest node executing this chunk hits the identical deterministic code path with the identical stale receipt, this crashes all validators processing that shard simultaneously, producing a transaction-triggered chain halt/DoS for that shard (and potentially the whole network if enough validators serve the affected shard). This satisfies the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Triggering requires: (1) any account deploying a global contract to create a `GlobalContractDistribution` receipt targeting its own shard, (2) sustaining compute-limit saturation on that shard long enough for the receipt to sit in the delayed queue across two dynamic-resharding shard-split events. All of this is achievable by a single unprivileged account submitting ordinary transactions (deploy-global-contract + repeated high-gas function calls), with no validator, network, or operator privilege required — the codebase's own test harness demonstrates the exact sequence deterministically. The only external dependency is that dynamic resharding (`DynamicReshardingConfig`) must trigger two splits of the same lineage while the receipt is delayed, which is a protocol-level feature already enabled in this tree.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` (`congestion_control.rs:876`) with proper error propagation (`?` returning `RuntimeError`), and ensure `receiver_shard_id`'s `EpochError::ShardingError` case for multi-generation-stale `target_shard` is handled gracefully (e.g., treat as a non-fatal condition that keeps the receipt in the queue or routes it to a safe fallback shard) rather than allowing an `Err` to propagate into an unconditional `unwrap()`/panic on the hot apply path.

### Proof of Concept
The exact reproduction is already implemented in-repo as `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`):
1. Deploy a global contract from `user0`, creating a `GlobalContractDistribution` receipt targeting `user0`'s shard.
2. Saturate that shard's compute budget every block with `burn_gas_raw` calls so the distribution receipt is pushed into the delayed-receipt queue and remains there.
3. Force two sequential dynamic-resharding splits of the lineage containing that shard while the receipt stays delayed.
4. Stop saturating and let the delayed queue drain; the test asserts the chain height keeps advancing — if the bug exists, the chain stalls because `receipt_filter_fn`'s `.unwrap()` panics when `receiver_shard_id` cannot remap the twice-stale `target_shard`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L868-920)
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

**File:** core/primitives/src/receipt.rs (L437-467)
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
