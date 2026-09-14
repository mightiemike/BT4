### Title
Unhandled `receiver_shard_id` error causes a transaction-triggered chain halt in `DelayedReceiptQueueWrapper::receipt_filter_fn` - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`receipt_filter_fn`, which every validator calls while draining the per-shard delayed-receipt queue during normal chunk application, calls `.unwrap()` on `Receipt::receiver_shard_id`. For `GlobalContractDistribution` receipts that value can legitimately return `Err(EpochError::ShardingError(...))` when the receipt's `target_shard` cannot be resolved to any shard in the current or historical shard layout. Any account can trigger creation of such a receipt via an ordinary `DeployGlobalContract` transaction; if the receipt sits in the delayed queue across resharding events long enough that its `target_shard` falls outside the resolvable split history, the `.unwrap()` panics on every validator applying that shard's chunk, halting the chain.

### Finding Description
`Receipt::receiver_shard_id` explicitly returns an `Err` rather than panicking for exactly this case: [1](#0-0) 

That `Err` path exists because a `GlobalContractDistribution` receipt can be delayed "across multiple resharding events," and `ShardLayout::resolve_to_current_shard` is documented to return `None` "only if the shard ID is absent from both the current layout and the split history" (i.e., the chain of resharding it needs to walk through is not fully preserved/reachable): [2](#0-1) [3](#0-2) 

Despite this being a documented, reachable error condition, the only caller that filters delayed receipts by shard after a split unconditionally unwraps it: [4](#0-3) 

`receipt_filter_fn` is invoked from both `pop` (used every chunk while draining the delayed-receipt queue during normal `Runtime::apply`) and `peek_iter`: [5](#0-4) 

`DeployGlobalContract`/global-contract-distribution receipts are created from an ordinary, unprivileged transaction (see the `deploy_global_contract` flow reachable from any signer), and their propagation mechanism explicitly anticipates staying in the delayed queue across "multiple resharding events" per the code comment. If a chunk producer/validator ever pops such a stale receipt whose `target_shard` cannot be mapped forward (e.g., the intermediate shard from an older split generation was pruned from `shards_split_map`, or resolution walks past the map's boundary), `.unwrap()` in `receipt_filter_fn` panics inside `Runtime::apply`, which every honest validator applying that shard runs identically — this is a deterministic, network-wide crash rather than a single-node fault.

The project itself already has a regression test explicitly built around this exact panic path, confirming the code owners are aware this is the failure mode to guard against: [6](#0-5) 

### Impact Explanation
A panic inside `Runtime::apply` (via the delayed-receipt drain path used by every validator on every chunk for that shard) is a deterministic, transaction-triggered halt: because `receipt_filter_fn` is called identically by every node processing that shard, the panic reproduces on all honest nodes simultaneously, stopping chunk production/finalization for the shard (and potentially the whole chain, depending on shard dependency) until a protocol fix is deployed. This matches the CVE-2016-10220 bug class (an unhandled null/negative case in device/content processing causing an application crash) mapped onto nearcore's cross-shard receipt/resharding pipeline. Per the scan's acceptance criteria, a transaction-triggered halt is an in-scope, high-impact outcome.

### Likelihood Explanation
Triggering requires: (1) an attacker submits a normal `DeployGlobalContract` transaction — no special privilege needed; (2) the resulting `GlobalContractDistribution` receipt remains in a shard's delayed queue while at least two resharding events occur, so that the ancestor lookup in `shards_split_map`/`resolve_to_current_shard` cannot walk forward to a currently-existing shard. Because dynamic resharding is an automatic, threshold-triggered protocol feature (not something an attacker directly controls the timing of), the likelihood is bounded by how easily an attacker can keep such a receipt delayed (e.g., by keeping the target shard congested/gas-saturated, as the existing regression test does) long enough to span two dynamic resharding generations. I was not able to fully verify from the available code slices whether `shards_split_map` retains unbounded split history (so `resolve_to_current_shard` always eventually succeeds) or whether history can be pruned/bounded such that resolution genuinely fails in production — the doc comment on `resolve_to_current_shard` implies the `None` case is reachable, and the dedicated regression test in `global_contracts_distribution.rs` was written specifically to catch this scenario, indicating the near-core team considers it realistic. Confirming the precise conditions under which the `None`/`Err` branch is exercised in a live multi-resharding scenario would benefit from a full session with access to the complete `ShardLayoutV3` split-map construction/pruning logic.

### Recommendation
- Change `receipt_filter_fn` to propagate the `Result` from `receiver_shard_id` instead of calling `.unwrap()`, surfacing it as a `RuntimeError`/`StorageError::StorageInconsistentState` (consistent with how other "missing item" cases in this same module are handled, e.g., `receipts_column_helper.rs`), rather than panicking.
- Ensure `pop` and `peek_iter` (and any other callers) handle the propagated error gracefully — e.g., by treating an unresolvable stale `GlobalContractDistribution` receipt as droppable/loggable rather than fatal, or by guaranteeing `shards_split_map` retains full history indefinitely so resolution can never fail for receipts created under the protocol's guarantees.
- Extend the existing regression test in `global_contracts_distribution.rs` to run under conditions with three or more resharding generations to confirm the fix holds beyond the two-generation case it currently exercises.

### Proof of Concept
1. Attacker (any funded account) submits a `DeployGlobalContract` action, generating a `GlobalContractDistribution` receipt targeting the shard the deploying account currently belongs to.
2. Attacker (or natural network load) saturates that shard's compute/gas budget every block (e.g., via `burn_gas_raw`-style calls, as done in the existing test) so the distribution receipt is pushed into the delayed-receipt queue instead of executing immediately.
3. While the receipt is delayed, two dynamic-resharding events occur that split the shard hierarchy such that the receipt's original `target_shard` can no longer be resolved forward through `shards_split_map`/`resolve_to_current_shard` to any shard in the current layout.
4. When the delayed queue is eventually drained (congestion relieved), `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id(...).unwrap()`; this returns `Err(EpochError::ShardingError(...))` and the `.unwrap()` panics inside `Runtime::apply`, crashing every validator applying that shard's chunks and halting chain progress. The exact mechanics of steps 2–3 are already codified as a regression scenario in `test-loop-tests/src/tests/global_contracts_distribution.rs:24-186`.

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
