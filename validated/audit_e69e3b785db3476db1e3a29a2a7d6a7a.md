### Title
Unrecoverable node panic on delayed `GlobalContractDistribution` receipts whose `target_shard` predates the shard-layout history - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`ShardLayoutV3::resolve_to_current_shard` (`core/primitives/src/shard_layout/v3.rs:320-326`) only resolves a historical `ShardId` if it is present either in the current shard list or in `shards_split_map`, which is a per-layout in-memory map built from a *bounded* `layout_history` window at derivation time (`build_shard_split_map`, `core/primitives/src/shard_layout/v3.rs:64-88`). `Receipt::receiver_shard_id` for `ReceiptEnum::GlobalContractDistribution` calls this resolver and returns `Err(EpochError::ShardingError(..))` when it fails [1](#0-0) . That `Result` is unconditionally `.unwrap()`ed inside `receipt_filter_fn`, called while draining the delayed-receipt queue during every chunk's normal `Runtime::apply` [2](#0-1) .

### Finding Description
A `GlobalContractDistribution` receipt stores a fixed numeric `target_shard` at creation time (from `action_deploy_global_contract`, reachable from any unprivileged account submitting a `DeployGlobalContract` action). If that receipt becomes congested/delayed and sits in the delayed-receipt queue while the network undergoes shard splits, `receipt_filter_fn`/`pop` (`runtime/runtime/src/congestion_control.rs:880-909`) is invoked on every chunk apply to decide whether the receipt belongs to the local shard. It calls `receiver_shard_id`, which for this receipt kind falls back to `shard_layout.resolve_to_current_shard(target_shard)` when `target_shard` is no longer part of the current shard set [3](#0-2) .

`resolve_to_current_shard` walks `shards_split_map`, which the code comment for `ShardsSplitMapV3` claims is a superset of all previous layouts' split maps and therefore a "full history" [4](#0-3) , but it is actually derived once from whatever `layout_history` slice was passed into `derive_with_layout_history` (from `next_next_shard_layout`, per the sharding-chunks spec) [5](#0-4) . If a receipt is delayed long enough to survive more shard splits than that history window covers, or if the shard being resolved predates the point where V3 layouts began accumulating split history (`build_shard_split_map` explicitly `break`s once it hits a pre-V3 layout, `core/primitives/src/shard_layout/v3.rs:71-73`), `resolve_to_current_shard` returns `None`, `receiver_shard_id` returns `Err`, and `receipt_filter_fn`'s `.unwrap()` panics.

This directly parallels the CVE-2017-5851 bug class: a cleanup/processing routine (`free_options`/here, `receipt_filter_fn`, called from the receipt-queue-draining `pop` path that is exercised on essentially every chunk) assumes a data structure is always fully resolvable and dereferences/unwraps it unconditionally, crashing the process when a legitimately-reachable, attacker-influenced input (a receipt whose `target_shard` is stale relative to the current shard layout) violates that assumption.

### Impact Explanation
A panic inside `Runtime::apply`'s receipt-processing loop is not contained to one transaction — it aborts the validator/chunk-producer process while applying the chunk, i.e. a transaction-triggered halt. Because all honest validators tracking the affected shard will independently hit the same delayed receipt and the same stale `target_shard`, this can simultaneously crash every node tracking that shard, stalling chain progress for that shard (denial of service at the protocol level), which is explicitly an accepted impact category (transaction-triggered halt).

### Likelihood Explanation
Triggering requires: (1) an unprivileged account deploying a global contract (`DeployGlobalContract`), which is a permissionless, ordinary action; (2) the resulting `GlobalContractDistribution` receipt getting congested/delayed via normal congestion control (attacker-controllable by saturating outgoing/receiving shard congestion, as demonstrated in the existing `global_contracts_distribution.rs` test harness that specifically engineers this scenario across shard splits); and (3) enough shard-layout churn (resharding events) occurring while the receipt is delayed to exceed whatever split-history depth was captured when the relevant `ShardLayoutV3` was derived. The nearcore test suite itself contains a regression test (`test-loop-tests/src/tests/global_contracts_distribution.rs:163-186`) built specifically to probe "processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations," confirming this is a known, previously-identified failure mode in this exact function. Whether the currently configured history-window depth and shard-split cadence make this practically reachable at genesis/protocol-version 86 is not something I could fully verify without running the resharding pipeline and tracing exactly how many `layout_history` entries `next_next_shard_layout` passes to `derive_with_layout_history` in production epoch configs — I flag this as an open verification point.

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:875-876`) with explicit error propagation (`Result` return from `pop`/`receipt_filter_fn`), and make `ShardLayoutV3`'s split-history construction retain the full lineage across all resharding generations (not just a bounded `layout_history` window), so `resolve_to_current_shard` cannot fail for any legitimately-emitted historical `target_shard`. As defense in depth, delayed receipts whose target shard cannot be resolved should be treated as an unrecoverable-but-handled protocol error (e.g., routed to a dead-letter/refund path) rather than causing a process panic.

### Proof of Concept
Conceptual reproduction (mirrors the existing test harness in `test-loop-tests/src/tests/global_contracts_distribution.rs`):
1. As an unprivileged account, submit `DeployGlobalContract` on a shard that is scheduled to split, producing a `GlobalContractDistribution` receipt with `target_shard` = the current (soon-to-be-retired) shard id.
2. Saturate congestion on the receiving path so the receipt is buffered/delayed rather than processed immediately (as done via `limit_outgoing_gas`/burn-gas contracts in the existing resharding test suite).
3. Drive the chain through enough additional epoch boundaries/resharding events that the split-history window captured by the currently active `ShardLayoutV3` no longer contains an entry mapping the receipt's `target_shard` forward to a live shard.
4. Let the delayed queue drain: `Runtime::apply` calls `pop` → `receipt_filter_fn` → `receiver_shard_id` → `resolve_to_current_shard` returns `None` → `Err` is `.unwrap()`ed → panic, halting every node applying that chunk for the shard.

### Citations

**File:** core/primitives/src/receipt.rs (L437-463)
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

**File:** core/primitives/src/shard_layout/v3.rs (L10-20)
```rust
/// A mapping from the parent shard to child shards. It maps shards from the
/// previous shard layout to shards that they split to in this shard layout.
/// Unlike previous versions of `ShardsSplitMap`, this one:
///   * Only includes shards that are actually split.
///   * Includes the full history of shard splits, i.e. split map of the current
///     layout is a superset of the split map of its parent layout.
///
/// For example if a shard layout with shards `[0, 2, 3, 4]` and split map `{1 => [3, 4]}`
/// splits shard 2 into shards [5, 6] the ShardSplitMap in the resulting layout will be:
/// `{1 => [3, 4], 2 => [5, 6]}`.
pub type ShardsSplitMapV3 = BTreeMap<ShardId, Vec<ShardId>>;
```

**File:** core/primitives/src/shard_layout/v3.rs (L242-256)
```rust
    /// Derive a V3 shard layout from an earlier version (V1/V2) using a sequence
    /// of previous shard layouts. The `layout_history` should be ordered from most
    /// recent to oldest.
    ///
    /// Returns an error if `new_boundary_account` already exists in `base_shard_layout`.
    pub fn derive_with_layout_history(
        base_shard_layout: &ShardLayout,
        new_boundary_account: AccountId,
        layout_history: &[ShardLayout],
    ) -> Result<Self, ShardLayoutError> {
        let shard_ids = base_shard_layout.shard_ids().collect();
        let boundary_accounts = base_shard_layout.boundary_accounts().clone();
        let shards_split_map = build_shard_split_map(layout_history);
        Self::derive_impl(shard_ids, boundary_accounts, new_boundary_account, shards_split_map)
    }
```
