### Title
Unhandled `receiver_shard_id` error is `.unwrap()`-ed in `receipt_filter_fn`, letting a single global-contract deploy transaction panic every validator processing the delayed queue (chain halt) - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
The Chainlink report's bug class is "trust an external/fallible call to always succeed and blindly `unwrap`/dereference its result instead of handling the `Err`, causing a permanent DoS once the call reverts." The analogous nearcore pattern is `ReceiptSinkV2`'s `receipt_filter_fn`, which calls `.unwrap()` on `Receipt::receiver_shard_id(&shard_layout)` — a fallible, `Result`-returning routine — while draining the delayed-receipt queue. Because this code runs identically and deterministically on every validator applying the same chunk, an `Err` there does not just fail one call, it panics every honest node at the same point, i.e. a transaction-triggered chain halt.

### Finding Description
`Receipt::receiver_shard_id` is fallible by design for `GlobalContractDistributionReceipt`s: if the receipt's `target_shard` is not present in the current shard layout, it tries to remap it via `shard_layout.resolve_to_current_shard(target_shard)`, and returns `Err(EpochError::ShardingError(...))` if that also fails: [1](#0-0) 

`resolve_to_current_shard` is only implemented for `ShardLayoutV3` (dynamic-resharding layouts with a cumulative split map); it is unavailable/`None` for `V0`/`V1`/`V2` static-resharding layouts, and even for V3 it only resolves shards reachable in `shards_split_map`: [2](#0-1) [3](#0-2) 

Most call sites correctly propagate this fallibility with `?` (e.g. buffer-forwarding in `ReceiptSinkV2::forward_from_buffer_to_shard`): [4](#0-3) 

But the delayed-queue drain path does not. `DelayedReceiptQueueWrapper::pop` uses `receipt_filter_fn`, a `bool`-returning filter closure (chosen "following the guidelines of standard iterator filter function"), which cannot propagate a `Result` and instead directly `.unwrap()`s both `shard_layout(&self.epoch_id)` and `receiver_shard_id(&shard_layout)`: [5](#0-4) 

This is exactly the "unhandled revert" pattern from the report: a call that can legitimately return an error (analogous to a Chainlink feed reverting) is called with an unwrap-style access instead of being handled defensively, and the failure path was reachable in production — the codebase itself contains a dedicated regression test explicitly built to reproduce a panic in `receipt_filter_fn()` via `receiver_shard_id()` failing to remap a stale `GlobalContractDistribution` `target_shard` after two resharding generations: [6](#0-5) [7](#0-6) 

The attack surface is fully reachable by an unprivileged transaction sender: deploying a global contract creates a `GlobalContractDistributionReceipt` with a `target_shard` (`runtime/runtime/src/global_contracts.rs:111-142`, `forward_distribution_next_shard`), and if that receipt is delayed (e.g. because the target shard's chunk is saturated with attacker-submitted `FunctionCall` gas-burning transactions) across one or more resharding events, its `target_shard` can become stale relative to the current `shard_layout`. When it is later popped from the delayed queue, `receipt_filter_fn` calls the fallible `receiver_shard_id`, and if the shard cannot be resolved (any layout not covered by V3's split-map heuristic — e.g. static/non-dynamic resharding layouts where `resolve_to_current_shard` is unavailable at all, or a resharding depth/path not captured in `shards_split_map`), the `.unwrap()` panics.

### Impact Explanation
This is not a local, catchable error — `apply_chunk`'s callers intentionally `panic!` on unexpected `StorageError`/`RuntimeError` variants that aren't explicitly whitelisted (`chain/chain/src/runtime/mod.rs:1296-1301` "Note that ... panicking here is better than leaking the exact details further up"), and the queue-draining panic here bypasses that error-classification path entirely by unwrapping inside the closure before any `RuntimeError` is even constructed. Because every validator that tracks the shard runs the identical deterministic chunk-apply code over the identical state, all of them hit the same `.unwrap()` at the same block height, deterministically crashing every node that processes that shard — a transaction-triggered chain halt, which is one of the explicitly in-scope high-impact outcomes (concrete transaction-triggered halt / invalid-state-transition class bug), not merely a resource-exhaustion nuisance.

### Likelihood Explanation
Reaching this path requires: (1) submitting a `DeployGlobalContract` transaction (unprivileged, any account can do this) whose distribution receipt targets a shard, and (2) getting that receipt delayed across shard-layout changes so that `target_shard` becomes unresolvable when finally popped. Delaying a receipt is achievable by submitting ordinary gas-heavy `FunctionCall` transactions to saturate the target shard's compute budget (exactly as done in the existing regression test), which is fully within reach of a single unprivileged signer with no special permissions, on any chain that performs shard splits (static or dynamic resharding). While the currently-known double-resharding scenario for V3 dynamic layouts appears to have been mitigated by `resolve_to_current_shard`, the `.unwrap()` itself remains the enforcement mechanism for *all* other cases (V0–V2 static layouts, or split-history paths not covered by the split map), so any future or existing resharding transition not perfectly modeled by `shards_split_map` reintroduces the exact same halt.

### Recommendation
Do not use a `bool`-returning filter closure with `.unwrap()` for a fallible operation. Change `receipt_filter_fn` (and its caller `DelayedReceiptQueueWrapper::pop`) to propagate `Result<bool, RuntimeError>` (or equivalent) instead of unwrapping `epoch_info_provider.shard_layout(...)` and `receiver_shard_id(...)`, so an unresolvable `target_shard` becomes a handled `RuntimeError`/`StorageInconsistentState` (as is already done in `forward_from_buffer_to_shard`) rather than an unconditional panic. As defense in depth, also make `resolve_to_current_shard`/`receiver_shard_id` robust for all `ShardLayout` versions (not just V3), or explicitly and safely drop/park undeliverable `GlobalContractDistribution` receipts instead of erroring, so that no delayed receipt can ever cause every node applying a given shard's chunk to crash simultaneously.

### Proof of Concept
1. Deploy a contract on account `A` in shard `S_A` of a multi-shard chain that supports resharding (static or dynamic).
2. Submit a `DeployGlobalContract` transaction from `A`; this creates a `GlobalContractDistributionReceipt` with `target_shard = S_A` (`runtime/runtime/src/global_contracts.rs`).
3. Every block, submit gas-maximizing `FunctionCall` transactions to shard `S_A` so its compute budget is saturated, forcing the incoming distribution receipt into the delayed queue (mirrors `test_stale_global_contract_distribution_after_double_resharding`, `test-loop-tests/src/tests/global_contracts_distribution.rs:30-131`).
4. Trigger shard-layout transitions (resharding events) while the receipt remains delayed, such that `S_A` no longer exists in the current layout and cannot be resolved by `resolve_to_current_shard`/`ancestor_uids` for the given layout version (any non-V3 layout, or a V3 split-history gap).
5. Stop saturating; when the runtime finally drains the delayed queue and calls `DelayedReceiptQueueWrapper::pop` → `receipt_filter_fn`, `receiver_shard_id(&shard_layout)` returns `Err(EpochError::ShardingError(...))`, the `.unwrap()` at `runtime/runtime/src/congestion_control.rs:876` panics, and every node applying that shard's chunk crashes — halting chain progress for that shard/chain.

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

**File:** core/primitives/src/shard_layout/mod.rs (L447-454)
```rust
    /// Get UIDs of all the shard's ancestors (parents, grandparents, etc.) for `ShardLayoutV3`.
    /// `None` for earlier versions.
    pub fn ancestor_uids(&self, shard_id: ShardId) -> Option<Vec<ShardUId>> {
        match self {
            Self::V0(_) | Self::V1(_) | Self::V2(_) => None,
            ShardLayout::V3(v3) => Some(v3.ancestor_uids(shard_id)),
        }
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L347-356)
```rust
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-68)
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L165-185)
```rust
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
