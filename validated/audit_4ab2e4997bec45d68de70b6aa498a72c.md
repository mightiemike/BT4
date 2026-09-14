## #Vulnerability found for this question.

### Title
Multi-generation resharding causes stale `GlobalContractDistribution` receipts to panic the runtime via `receiver_shard_id()`/`receipt_filter_fn` — chain halt on static (V0/V1/V2) shard layouts - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
Any account can submit a `DeployGlobalContract` transaction, which the runtime turns into a `GlobalContractDistribution` receipt carrying a fixed `target_shard` field [1](#0-0) . If that receipt is delayed (e.g. due to congestion) across **two or more** shard-splitting events on a shard layout that is not `ShardLayoutV3` (i.e. classic static resharding, `V0`/`V1`/`V2`), the shard-remapping logic `ShardLayout::resolve_to_current_shard()` can fail to find the receipt's current shard, causing `Receipt::receiver_shard_id()` to return `Err`, which is then `.unwrap()`-ed inside `DelayedReceiptQueueWrapper::receipt_filter_fn`, panicking every node that processes the delayed-receipt queue for that shard.

### Finding Description
`Receipt::receiver_shard_id()` special-cases `GlobalContractDistribution` receipts: if the receipt's stored `target_shard` is not part of the current shard layout, it calls `shard_layout.resolve_to_current_shard(target_shard)` to walk forward through resharding generations and find the current descendant shard: [2](#0-1) 

`ShardLayout::resolve_to_current_shard()` dispatches per shard-layout version: [3](#0-2) 

For `ShardLayoutV3` (dynamic resharding), the full cumulative split history is stored (`shards_split_map`), so `resolve_to_current_shard` can walk arbitrarily many generations back-to-current: [4](#0-3) 

But for the older `V0`/`V1`/`V2` static-resharding layouts, the fallback implementation only consults `get_children_shards_ids`, which the code comment explicitly documents as supporting **only a single generation**: [5](#0-4) 

If a target shard was split twice (grandparent → parent → current) under static resharding, `get_children_shards_ids(grandparent)` on the current (twice-derived) layout returns `None`, so `resolve_to_current_shard` returns `None`, and `receiver_shard_id()` returns `Err(EpochError::ShardingError(...))`.

That `Err` is then unconditionally unwrapped in the delayed-receipt filtering logic that every chunk-processing node executes while draining the delayed receipt queue: [6](#0-5) 

This `receipt_filter_fn` is invoked from `DelayedReceiptQueueWrapper::pop`, which is called during normal receipt processing for every chunk (`Runtime::apply` → `process_receipts` → delayed receipt queue draining): [7](#0-6) 

The repository's own regression test acknowledges this exact failure mode and explicitly documents that the mitigation (full split-history tracking) only applies to `ShardLayoutV3`/dynamic resharding, and is skipped for static resharding: [8](#0-7) [9](#0-8) 

### Impact Explanation
Any node that pops the stale `GlobalContractDistribution` receipt from its delayed-receipt queue will `panic!` inside `Runtime::apply`, which is invoked from the chunk-application path used by every validator/RPC node tracking that shard. Because `Runtime::apply` runs deterministically for all honest nodes on the same input, this is not a crash isolated to one operator — every node that must apply that shard's chunk containing the drained delayed receipt will panic identically, producing a network-wide, transaction-triggered halt of chunk production/finalization for the affected shard (and consequently the chain), matching the "transaction-triggered halt" impact category. No attacker privilege beyond submitting an ordinary `DeployGlobalContract` transaction and causing shard congestion is required.

### Likelihood Explanation
Reaching this requires: (1) submitting a `DeployGlobalContract` transaction that produces a `GlobalContractDistribution` receipt whose `target_shard` is the shard about to be resharded, (2) congesting that shard so the receipt sits in the delayed queue while two successive shard splits happen on a non-V3 (static) shard layout, and (3) the delayed queue eventually being drained. Steps 1 and 2 (deploy + congest) are fully within reach of any unprivileged transaction sender/contract deployer. Step 3 (two static reshardings occurring while the specific receipt is delayed) is timing-dependent and only occurs on deployments still using static (`V0`/`V1`/`V2`) shard layouts rather than dynamic (`V3`) resharding — this narrows likelihood but the vulnerable code path (`resolve_to_current_shard` for non-V3 layouts) remains live in the codebase and is only avoided by policy (using dynamic resharding), not by a code-level fix.

### Recommendation
Either (a) extend `ShardLayout::resolve_to_current_shard()` for `V0`/`V1`/`V2` to walk the full multi-generation split history (mirroring the V3 fix), reconstructing it from `EpochManagerAdapter::get_shard_layout_history()` as is already done elsewhere for V3 bootstrapping, or (b) replace the `.unwrap()` in `receipt_filter_fn` (and any other caller of `receiver_shard_id()` in the hot apply path) with graceful error handling that surfaces a recoverable `RuntimeError` instead of panicking, so a stale/unresolvable `target_shard` cannot halt chunk application.

### Proof of Concept
1. Configure a network with a static (`V1`/`V2`) `ShardLayoutConfig`.
2. From any account, submit a `DeployGlobalContract` transaction targeting a shard `S` (creates a `GlobalContractDistribution` receipt with `target_shard = S`).
3. Congest shard `S` (e.g., repeated `burn_gas_raw`-style heavy transactions) so the distribution receipt is pushed into the delayed-receipt queue and stays there.
4. Trigger two consecutive protocol-version-driven static reshardings that split `S`'s descendant shard again before the receipt is drained (mirrors `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs`, but without the `DynamicResharding` early-return guard).
5. Let congestion subside so the delayed queue drains; `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id()` → `resolve_to_current_shard()` → `None` → `Err` → `.unwrap()` panics, halting chunk application for shard `S` on every node.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L275-304)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-39)
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
