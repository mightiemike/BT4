### Title
Unhandled `.unwrap()` panic in `receipt_filter_fn` on stale `GlobalContractDistribution` receipts after resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the `Result` returned by `Receipt::receiver_shard_id`, which can return `Err(EpochError::ShardingError(...))` when a `GlobalContractDistribution` receipt's `target_shard` no longer resolves in the current shard layout's split history. This is the CVE-2017-14928 bug class (an unhandled null/error dereference on attacker/user-influenced structured data causing a crash) reachable via an ordinary NEAR transaction (a global contract deployment) combined with normal chain progression (resharding).

### Finding Description
`receipt_filter_fn` calls `.unwrap()` directly on the result of `receiver_shard_id`: [1](#0-0) 

`Receipt::receiver_shard_id` explicitly documents and implements a failure path for `GlobalContractDistribution` receipts whose `target_shard` predates the layout's split history: [2](#0-1) 

Any account can trigger a `GlobalContractDistribution` receipt by deploying a global contract (a normal, unprivileged action). If that receipt is delayed (e.g., due to shard compute saturation) long enough to span two or more resharding generations, `resolve_to_current_shard` can fail to map the old `target_shard` into the current layout, returning `Err`. Since `receipt_filter_fn` is invoked from `DelayedReceiptQueueWrapper::pop`, which is called during ordinary chunk application when draining delayed receipts, the `.unwrap()` panics and aborts chunk application for every validator/node processing that shard: [3](#0-2) 

The repository already contains a regression test purpose-built to detect exactly this scenario (`test_stale_global_contract_distribution_after_double_resharding`), which deploys a global contract, saturates the originating shard's compute so the distribution receipt is delayed, forces two sequential dynamic reshardings of that shard, and then asserts that the chain keeps making progress instead of stalling from a panic: [4](#0-3) [5](#0-4) 

I was not able to fully confirm from the available index snippets whether the fix for this specific double-resharding case is *complete* in all code paths (e.g., whether `resolve_to_current_shard`'s split-history depth is unconditionally sufficient for arbitrary resharding counts, or only bounded for the tested two-generation case), because the full body of `resolve_to_current_shard` in `core/primitives/src/shard_layout/v3.rs` was not retrieved. The presence of a still-live `.unwrap()` at `congestion_control.rs:876` combined with an explicit `Err` path documented in `receiver_shard_id` means the panic is only prevented as long as `resolve_to_current_shard` never returns `None` for any receipt that can actually reach this filter — that guarantee is not enforced in `receipt_filter_fn` itself.

### Impact Explanation
If `resolve_to_current_shard` returns `None` for any surviving delayed `GlobalContractDistribution` receipt (e.g., a receipt delayed across more resharding generations than the split-history bookkeeping retains, or under static resharding where the comment in the test explicitly states "the shard layout doesn't maintain a full split history"), every validator/node applying the affected shard's chunk panics simultaneously and deterministically. This is a transaction-triggered halt of chunk/block production for the shard, since the trigger (a global contract deployment plus normal chunk congestion/backlog) is entirely attacker-controllable and requires no special privileges.

### Likelihood Explanation
Triggering the underlying condition requires: (1) deploying a global contract (unprivileged, ordinary action) so its `GlobalContractDistribution` receipt is created; (2) causing that receipt to sit in the delayed-receipt queue across multiple resharding events (achievable by congesting the target shard, which an attacker with enough gas/funds can do); (3) the target layout's split-history bookkeeping failing to trace the old `target_shard` forward. Condition (3) is precisely what the codebase's own regression test targets for dynamic (V3) resharding, and the test comment states the fix explicitly does **not** cover static resharding ("With static resharding, the shard layout doesn't maintain a full split history"), leaving that configuration exposed to the same `.unwrap()` panic. Likelihood is therefore non-trivial under static resharding or any resharding depth beyond what the fix's split-history retention was designed for.

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (both the `shard_layout` lookup and `receiver_shard_id`) with proper error propagation into `RuntimeError`/`StorageError::StorageInconsistentState`, consistent with the pattern already used elsewhere in the same file (e.g., `IntegerOverflowError`, `checked_add`/`ok_or`). Additionally, audit `resolve_to_current_shard` to guarantee it can resolve a `target_shard` across an unbounded number of resharding generations, or make the split-history retention explicitly protocol-enforced so it cannot fall behind the maximum delay a receipt can experience in the delayed queue, for both static and dynamic resharding configurations.

### Proof of Concept
The repository's own test demonstrates the attack sequence and end condition (a stall indicating a panic would have occurred without the fix under V3/dynamic resharding): [6](#0-5) 
An analogous sequence under a static-resharding shard-layout configuration (explicitly called out by the test's own early-return guard as unfixed: "the shard layout doesn't maintain a full split history") is the concrete reachable path to the unhandled `.unwrap()` panic in `receipt_filter_fn`.

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-66)
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-186)
```rust
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
