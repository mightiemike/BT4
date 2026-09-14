### Title
Panic-inducing `.unwrap()` on `receiver_shard_id()` in `receipt_filter_fn` crashes chunk producers processing stale cross-shard `GlobalContractDistribution` receipts after resharding - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::pop` filters delayed receipts via `receipt_filter_fn`, which calls `.unwrap()` on `Receipt::receiver_shard_id(&shard_layout)`. For `GlobalContractDistribution` receipts, `receiver_shard_id` returns `Err(EpochError::ShardingError(...))` when the receipt's `target_shard` cannot be resolved to a shard in the current layout via `ShardLayout::resolve_to_current_shard`. On V0/V1/V2 layouts (produced by static resharding), `resolve_to_current_shard` only walks a *single* generation of splits (`get_children_shards_ids`), unlike `ShardLayoutV3` which walks the *full* cumulative split history. A distribution receipt delayed long enough to span two resharding events under a non-V3 layout will make `resolve_to_current_shard` return `None`, `receiver_shard_id` return `Err`, and the subsequent `.unwrap()` panic — crashing every node that attempts to apply that shard's chunk.

### Finding Description
Any account can trigger a `GlobalContractDistribution` receipt by deploying a global contract (`DeployGlobalContractAction`). This receipt is fanned out shard-by-shard with a `target_shard` field fixed at creation time: [1](#0-0) 

When a chunk is congested, such receipts get pushed into the persistent delayed-receipt queue and are later popped in `DelayedReceiptQueueWrapper::pop`, which calls `receipt_filter_fn` to decide whether the popped receipt still belongs to the current shard (needed because ReshardingV3 can retarget receipts): [2](#0-1) 

`receipt_filter_fn` unconditionally unwraps the result of `receiver_shard_id`: [3](#0-2) 

`Receipt::receiver_shard_id` for `GlobalContractDistribution` receipts falls back to `shard_layout.resolve_to_current_shard(target_shard)`, explicitly returning an `Err` (not panicking itself) when the target shard is absent "from the shard layout or its split history": [4](#0-3) 

`ShardLayout::resolve_to_current_shard` dispatches differently by layout version: [5](#0-4) 

- For `ShardLayoutV3`, it recursively walks the *cumulative* `shards_split_map`, correctly resolving a shard through arbitrarily many resharding generations: [6](#0-5) 
- For `V0`/`V1`/`V2`, it only calls `get_children_shards_ids`, which resolves at most one generation of splits (the most recent one relative to that layout instance).

A regression test in the repository confirms the developers are aware this exact panic path exists, and explicitly scopes their fix to V3 dynamic-resharding layouts only: [7](#0-6) [8](#0-7) 

The test's own comment states: *"The fix only works with V3 shard layouts (dynamic resharding). With static resharding, the shard layout doesn't maintain a full split history."* This means static resharding (protocol-version-driven layout changes, still a supported and used code path per `docs/architecture/how/dynamic_resharding.md`) is **not** covered by the fix, and a `GlobalContractDistribution` receipt that is delayed across two static-resharding boundaries will still hit the `Err` branch and panic via the unguarded `.unwrap()` in `receipt_filter_fn`.

### Impact Explanation
This is a transaction/action-triggered halt: an unprivileged account only needs to submit a `DeployGlobalContract` (global contract deployment) transaction whose resulting `GlobalContractDistribution` receipt gets delayed (e.g., by congesting the target shard) so that it survives two shard-layout transitions. When the delayed queue is later drained, `receipt_filter_fn`'s `.unwrap()` panics deterministically on every honest validator/chunk-producer applying that shard, since the shard layout and delayed-receipt state are part of consensus and identical across all nodes. This crashes chunk production for the affected shard — a chain halt / persistent denial of service, not merely a resource issue, matching the CVE-2018-20198 bug class (unhandled null/error case → crash → DoS) but reachable purely through normal transaction/receipt processing rather than a malformed external input.

### Likelihood Explanation
Reaching this bug requires: (1) submitting a `DeployGlobalContract` transaction (trivial, unprivileged), and (2) the resulting distribution receipt being delayed long enough (via congestion) to span two resharding events on a shard still using V0/V1/V2 layouts. Static resharding transitions are infrequent (tied to protocol upgrades), so the practical window depends on how long receipts can be held in delayed queues combined with how often static resharding recurs. The root-cause code path (`.unwrap()` on a documented `Err` case) is unconditional and unguarded, so likelihood is bounded only by achieving the timing/queue-depth precondition, which an attacker partially controls by congesting a target shard to prolong the delay.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (`?` through a `Result`-returning `pop`, or fail-safe handling such as treating an unresolved shard as belonging to the current shard) so that a failed `receiver_shard_id` resolution returns a `RuntimeError` instead of panicking. Additionally, extend the split-history tracking used by `resolve_to_current_shard` for non-V3 layouts (or force full migration to V3 before allowing further static resharding) so multi-generation resolution works uniformly regardless of layout version.

### Proof of Concept
Not independently reproduced with a live cluster in this analysis (no execution environment available), but the exact scenario is already encoded as an existing regression test in the repo demonstrating the panic condition: [9](#0-8) [8](#0-7) 
This test only guards the V3 dynamic-resharding case (per its own comment); the analogous scenario with V0/V1/V2 static-resharding layouts undergoing two splits while a `GlobalContractDistribution` receipt is delayed is untested and, based on the `resolve_to_current_shard` implementation difference cited above, remains exploitable. Confirming this conclusively would require constructing a static-resharding test analogous to the existing dynamic one and observing the panic in `receipt_filter_fn`.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L111-141)
```rust
pub(crate) fn apply_global_contract_distribution_receipt(
    receipt: &Receipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<Compute, RuntimeError> {
    let _span = tracing::debug_span!(
        target: "runtime",
        "apply_global_contract_distribution_receipt",
    )
    .entered();

    let ReceiptEnum::GlobalContractDistribution(global_contract_data) = receipt.receipt() else {
        unreachable!("given receipt should be an global contract distribution receipt")
    };
    let compute =
        apply_distribution_current_shard(receipt, global_contract_data, apply_state, state_update)?;
    forward_distribution_next_shard(
        receipt,
        global_contract_data,
        apply_state,
        epoch_info_provider,
        state_update,
        receipt_sink,
        receipt_to_tx,
    )?;

    Ok(compute)
}
```

**File:** runtime/runtime/src/congestion_control.rs (L868-909)
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

**File:** core/primitives/src/shard_layout/mod.rs (L227-237)
```rust
    /// Resolve any historical shard ID to a current descendant shard ID.
    /// For V3 layouts this uses the complete `shards_split_map`.
    /// For older layouts it falls back to `get_children_shards_ids` (single generation only).
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        match self {
            Self::V0(_) | Self::V1(_) | Self::V2(_) => {
                self.get_children_shards_ids(shard_id).map(|c| c[0])
            }
            Self::V3(v3) => v3.resolve_to_current_shard(shard_id),
        }
    }
```

**File:** core/primitives/src/shard_layout/v3.rs (L315-326)
```rust
    /// Resolve any historical shard ID to a current shard by walking the full
    /// split history in `shards_split_map`. Returns the shard itself if it is
    /// current, or follows the first child at each generation until a current
    /// shard is reached. Returns `None` only if the shard ID is absent from
    /// both the current layout and the split history.
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        if self.shard_ids.contains(&shard_id) {
            return Some(shard_id);
        }
        let children = self.shards_split_map.get(&shard_id)?;
        self.resolve_to_current_shard(children[0])
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-100)
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
