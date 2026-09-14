### Title
Unprivileged global contract deployment can permanently halt the chain via an unhandled panic in delayed-receipt shard filtering after shard resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`runtime/runtime/src/congestion_control.rs`'s `receipt_filter_fn` calls `.unwrap()` on `Receipt::receiver_shard_id`, a function that is explicitly documented and implemented to return an `Err` when a `GlobalContractDistributionReceipt`'s `target_shard` cannot be mapped to any shard in the current or historical shard layout. [1](#0-0)  Any unprivileged account can trigger the creation of such a receipt simply by deploying a global contract, and if the receipt is delayed long enough across shard-layout changes, the shard lookup can fail and the resulting `Err` is turned into an unconditional panic that every honest validator applying that chunk will hit at the same block height.

### Finding Description
`Receipt::receiver_shard_id` resolves the target shard for a `GlobalContractDistribution` receipt. If the stored `target_shard` is not present in the current layout, it falls back to `ShardLayout::resolve_to_current_shard`, and only returns `Err(EpochError::ShardingError(...))` if that also fails: [2](#0-1) 

`ShardLayout::resolve_to_current_shard` dispatches per layout version: [3](#0-2) 

For `V3` layouts the resolution walks the *complete* cumulative split history (`shards_split_map`), so it can resolve a shard id from arbitrarily many resharding generations back: [4](#0-3) 

However, for the older `V0`/`V1`/`V2` layouts — which are still the layout kind used before `ProtocolFeature::DynamicResharding` is active, or whenever a network uses static (protocol-version-driven) resharding — resolution only follows **a single generation** of children (`get_children_shards_ids(shard_id).map(|c| c[0])`), with no deeper history walk: [3](#0-2) 

If a `GlobalContractDistribution` receipt is delayed (pushed to the delayed-receipt queue because the target shard is congested/compute-saturated) across **two or more** static shard splits, `target_shard` will no longer exist in the current layout and will also not exist as a direct child in the (single-generation) split map, so `resolve_to_current_shard` returns `None`, and `receiver_shard_id` returns `Err(EpochError::ShardingError(...))`.

The caller `receipt_filter_fn`, used by the delayed-receipt queue's `pop()` to decide which receipts belong to the current shard during resharding, does not handle this `Err` — it unconditionally `.unwrap()`s it: [5](#0-4) 

This is the exact bug class the analog test in this repository was written to catch — a stale/delayed `GlobalContractDistribution` receipt panicking `receipt_filter_fn`/`receiver_shard_id` after resharding — but that regression test and the fix it validates apply specifically to `V3` (dynamic resharding) layouts, whose full split history the code above correctly walks: [6](#0-5) 

The single-generation fallback path for `V0`/`V1`/`V2` layouts was not given the same fix, leaving the underlying panic reachable whenever a network relies on legacy/static shard-layout versions and experiences two or more shard splits while a global contract distribution receipt is delayed.

### Impact Explanation
`process_delayed_receipts`/the delayed-receipt queue's `pop()` runs deterministically inside `Runtime::apply` on every validator applying the affected chunk. A panic here is not contract-scoped (unlike a WASM guest panic caught by the VM) — it aborts the runtime `apply` call itself, causing the node process to crash/abort chunk application. Because the same delayed queue state and shard layout are shared by all honest validators tracking that shard, every validator hits the panic at the same block height, producing a deterministic, network-wide **chain halt** — a `neard` process crash that stops chunk/block production for the shard, requiring manual intervention/patch to recover. This satisfies the "transaction-triggered halt" impact accepted by the validation criteria.

### Likelihood Explanation
Reaching this bug requires: (1) deploying a global contract (`DeployGlobalContract`/`GlobalContractDeployMode`), an action any unprivileged account can submit; (2) the resulting `GlobalContractDistributionReceipt` becoming delayed (e.g., by saturating the target shard's compute budget, as demonstrated feasible in the analogous V3 test); and (3) at least two shard splits occurring on a `V0`/`V1`/`V2` (static/legacy) shard layout while the receipt remains delayed. This is a non-trivial but fully attacker-controllable sequence — it does not require validator collusion, leaked keys, or any privileged role, only ordinary transactions plus naturally-occurring (or, on a test/staging network, force-triggered) shard splits. Networks that have not yet activated `DynamicResharding` (still on static/legacy layouts) or that fall back through legacy layout versions during migration are exposed.

### Recommendation
- Make `receipt_filter_fn` propagate the `Result` from `receiver_shard_id` instead of `.unwrap()`ing it, treating an unresolved shard mapping as a recoverable error (e.g., drop/skip the receipt with a tracked error, or return `Result` from `pop()`).
- Extend `ShardLayout::resolve_to_current_shard`/`get_children_shards_ids` for `V0`/`V1`/`V2` layouts to walk multiple generations of splits (or otherwise guarantee that legacy layouts retain enough split history to resolve any shard id that could still be referenced by an in-flight delayed receipt), matching the guarantee already provided for `V3`.
- Add a regression test analogous to `test_stale_global_contract_distribution_after_double_resharding` but exercising two static (V2) resharding events with a global contract distribution receipt delayed across both, to confirm the fallback path no longer panics.

### Proof of Concept
1. Start a network using static (non-`DynamicResharding`) shard layout versions (`V1`/`V2`), configured so that two shard-layout changes will occur (e.g., two protocol-version upgrades that each split a shard).
2. From an ordinary account, submit a `DeployGlobalContract` action targeting an account on the shard about to be split first; this creates a `GlobalContractDistributionReceipt` with `target_shard` = that shard's id.
3. Before the receipt is processed, saturate that shard's per-chunk compute budget every block (e.g., repeated `burn_gas_raw`-style calls) so the distribution receipt is pushed into the delayed-receipt queue, as done in the existing V3 test: [7](#0-6) 
4. Let the first shard split occur (target shard id no longer exists in the layout, but is a direct child-mapped ancestor — `resolve_to_current_shard` still finds it via the single-generation lookup).
5. Before draining the delayed queue, let a second shard split occur affecting the same lineage, so `target_shard` is now two generations removed from any id in the current `V1`/`V2` layout.
6. Stop saturating and let the delayed queue drain: `receipt_filter_fn`'s call to `receiver_shard_id(&shard_layout).unwrap()` returns `Err`, and `.unwrap()` panics inside `Runtime::apply`, crashing chunk application on every validator tracking that shard.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L868-910)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-131)
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
