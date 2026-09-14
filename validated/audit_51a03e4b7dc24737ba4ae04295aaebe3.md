### Title
Node halt via unhandled `.unwrap()` panic in `receipt_filter_fn` when a `GlobalContractDistribution` receipt's target shard cannot be resolved after resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`receipt_filter_fn` in the delayed-receipt queue drain path calls `receiver_shard_id(&shard_layout).unwrap()` on every delayed receipt popped for processing. `receiver_shard_id` returns `Err(EpochError::ShardingError(..))` for `GlobalContractDistribution` receipts whose `target_shard` cannot be found in the current shard layout nor resolved via `ShardLayoutV3::resolve_to_current_shard`. Any code path that can leave such a receipt "stale" (target shard permanently absent from both the live layout and its split history) turns an ordinary chunk-application step into a Rust panic, which — unlike `InvalidTxError`/`ReceiptValidationError`, which are converted into graceful errors elsewhere in the apply pipeline — is not caught, and takes down chunk production/validation for that shard.

### Finding Description
`receiver_shard_id` on `Receipt` resolves the shard a receipt should be delivered to: [1](#0-0) 
For `GlobalContractDistribution` receipts, if `target_shard` is not part of the current `shard_layout` it falls back to `shard_layout.resolve_to_current_shard(target_shard)`, and only returns an error if that also fails.

`resolve_to_current_shard` recursively walks `shards_split_map` following the *first* child at each generation: [2](#0-1) 
This mapping is built only from consecutive shard layouts kept in the epoch-manager's layout history via `build_shard_split_map`, which explicitly `break`s once it hits a shard layout whose `version() < VERSION` (i.e., legacy V1/V2 layouts) — the split history is *not* guaranteed to reach arbitrarily far back: [3](#0-2) 

The consumer of this is `DelayedReceiptQueueWrapper::receipt_filter_fn`, called from `pop()` in the hot receipt-draining loop that every chunk producer/validator executes when applying a chunk: [4](#0-3) 
Note the `.unwrap()` on line 876 — any `Err` from `receiver_shard_id` becomes an unconditional panic, not a `RuntimeError` that `Runtime::apply`'s caller can translate to `Error::InvalidTransactions` or similar. Compare this to how `Runtime::apply`'s other error variants are handled in `chain/chain/src/runtime/mod.rs`, where only `RuntimeError::ReceiptValidationError`/`UnexpectedIntegerOverflow` are deliberately (if crudely) `panic!`'d as "should never happen" invariants — `receipt_filter_fn`'s panic bypasses even that error-typing layer entirely, occurring deep inside congestion-control bookkeeping with no `Result` propagation path at all.

A `GlobalContractDistribution` receipt's `target_shard` is forwarded shard-by-shard via `forward_distribution_next_shard` in `runtime/runtime/src/global_contracts.rs`, and any receipt of this kind that misses being applied in-band (e.g., queued as delayed across a resharding boundary) is exactly the receipt kind this filter exists to protect: the code comment above `receipt_filter_fn` states this directly: [5](#0-4) 
There is already a regression test (`test_stale_global_contract_distribution_after_double_resharding`) acknowledging and probing exactly this panic risk after *two* resharding generations: [6](#0-5) 
However this test only exercises the "double resharding, single split-chain" scenario that `resolve_to_current_shard`'s single-first-child recursive walk *does* handle correctly given the accumulated `shards_split_map`. The residual risk is any scenario where the split history available to `receiver_shard_id`'s shard layout (bounded by `build_shard_split_map`'s epoch-manager-supplied `layout_history`, and by the V1/V2→V3 break condition) does not contain an unbroken chain from the receipt's stale `target_shard` down to a currently-live shard — e.g. deep chains of resharding events that outlive the layout-history window retained by the epoch manager, or any resharding across the V1/V2/V3 version boundary. In that situation `resolve_to_current_shard` returns `None`, `receiver_shard_id` returns `Err`, and `receipt_filter_fn`'s `.unwrap()` panics unconditionally while draining the delayed-receipt queue for every subsequent chunk that shard produces — an unrecoverable, transaction-triggered halt (the delayed receipt stays in the queue and is re-encountered on every future `pop()` call, so the shard can never make progress again).

### Impact Explanation
This is reachable by any unprivileged account that deploys a global contract (`DeployGlobalContractAction`, a standard, permissionless transaction action) whose distribution receipt happens to be delayed across a resharding boundary that exceeds the bounded split-history window. Because the panic occurs inside `Runtime::apply`'s receipt-draining loop, which every chunk producer and every chunk validator executes deterministically, the affected shard's chunk production halts entirely — a transaction-triggered denial of service that requires no adversarial coordination beyond timing a global-contract deploy against a dynamic-resharding schedule. This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Exploitability is contingent on how far back the epoch manager retains shard-layout history for `build_shard_split_map`, and on whether any resharding path crosses the legacy V1/V2 → V3 version boundary (which is explicitly excluded from the split map). I was not able to fully verify, within the available index, the exact retention window or every code path that constructs the `layout_history` passed into `derive_with_layout_history`/`build_shard_split_map`, so I cannot confirm the precise number of resharding generations or elapsed epochs required to trigger the unresolved case in production configuration. The existence of a dedicated regression test for the "double resharding" case strongly suggests this exact class of panic has already been identified as a real risk by the nearcore team, which increases confidence that further/deeper resharding sequences (or crossing the legacy layout boundary) are a genuine unpatched edge of the same bug class, though I could not construct a concrete end-to-end trigger sequence beyond what the existing test already covers.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (`?` returning `RuntimeError`), and ensure `Runtime::apply`'s caller treats an unresolvable stale receipt as a recoverable condition (e.g., permanently retire/burn the receipt with an outcome, or keep it queued without panicking) rather than crashing the node. Additionally, extend `build_shard_split_map`/`layout_history` retention (or otherwise persist the full split lineage independent of epoch-manager history pruning and the V1/V2/V3 version boundary) so `resolve_to_current_shard` can always resolve any historical shard id that a still-undelivered receipt may reference, no matter how many resharding generations or version transitions have elapsed.

### Proof of Concept
Not independently constructed beyond the existing regression test; a workable outline mirrors `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs` (lines 30-186), but extended to force three or more sequential dynamic-resharding splits (or a resharding that crosses from a V1/V2 base layout to V3) targeting the shard holding a pending `GlobalContractDistribution` receipt, then draining the delayed-receipt queue and observing the chunk producer panic in `receipt_filter_fn` instead of the chain continuing to advance.

### Citations

**File:** core/primitives/src/receipt.rs (L437-465)
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
```

**File:** core/primitives/src/shard_layout/v3.rs (L60-88)
```rust
/// Build `ShardsSplitMapV3` from a sequence of previous shard layouts.
///
/// Assumes that layouts are ordered from newest to oldest, and that there are no duplicates.
/// Ignores layouts with `version()` lower than `VERSION` (this is **not** the struct version).
pub fn build_shard_split_map(layout_history: &[ShardLayout]) -> ShardsSplitMapV3 {
    let mut split_history = ShardsSplitMapV3::new();

    for window in layout_history.windows(2) {
        let current_layout = &window[0];
        let prev_layout = &window[1];

        if current_layout.version() < VERSION || prev_layout.version() < VERSION {
            break;
        }

        debug_assert_ne!(current_layout, prev_layout);

        for shard_id in current_layout.shard_ids() {
            match current_layout.try_get_parent_shard_id(shard_id).expect("invalid shard_id") {
                Some(parent_id) if parent_id != shard_id => {
                    split_history.entry(parent_id).or_default().push(shard_id);
                }
                _ => continue,
            }
        }
    }

    split_history
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

**File:** runtime/runtime/src/congestion_control.rs (L868-911)
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
