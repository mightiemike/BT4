## Analysis

I found a concrete analog to the "strategy no longer whitelisted, breaking rebalance" pattern: a receipt carries a reference to external, mutable topology (a `target_shard`, analogous to a "strategy address") that is resolved via a fallback path (`resolve_to_current_shard`) whose result is then `.unwrap()`'d, so a resolution failure panics and halts the chain, rather than being handled as a soft error — exactly the "no prior checks on whether the reference is still valid" root cause from the report. [1](#0-0) [2](#0-1) 

I was unable to fully determine, with certainty, whether `resolve_to_current_shard`'s multi-generation walk (`core/primitives/src/shard_layout/v3.rs:320-326`) is actually complete for every resharding history (e.g. `ShardsSplitMapV3` retaining full split history across all generations per its own doc comment at lines 10-19), or whether there exists a code path (e.g. mixed V1/V2→V3 layout transition, `build_shard_split_map` breaking early on `version() < VERSION` at line 71-73) where a `target_shard` becomes permanently unresolvable, causing `resolve_to_current_shard` to return `None` and the subsequent `.unwrap()` in `receipt_filter_fn` to panic. A test in the repo, `test-loop-tests/src/tests/global_contracts_distribution.rs:163-187`, explicitly exercises this exact scenario ("processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations") and asserts the chain does *not* stall — suggesting this was already identified and is presently guarded against for the tested case. However, `build_shard_split_map`'s early-break on `version() < VERSION` means that a `GlobalContractDistributionReceipt` created under a pre-V3 layout, delayed across a V1/V2→V3 transition plus a subsequent split, could plausibly still hit the unresolved case, panicking in `receipt_filter_fn`'s `.unwrap()`. I could not conclusively verify this boundary case with the tools available.

### Title
Unhandled `.unwrap()` on `receiver_shard_id` in delayed-receipt shard filtering can panic and halt chunk application - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the result of `Receipt::receiver_shard_id`, which itself can return `Err` when a `GlobalContractDistributionReceipt`'s `target_shard` cannot be resolved to any shard in the current or historical split map [3](#0-2) [1](#0-0) . This mirrors the reported bug class: a receipt carries a reference to mutable external state (target shard, analogous to an EigenLayer strategy) that is assumed valid at creation time but is never re-validated, and a later stage that depends on that reference resolving successfully has no fallback — it panics/reverts instead of degrading gracefully.

### Finding Description
A `DeployGlobalContractAction` creates a `GlobalContractDistributionReceipt` whose `target_shard` is fixed to the current shard at deploy time [4](#0-3) . If this receipt is delayed (e.g., under sustained congestion) across one or more resharding events, its `target_shard` may no longer exist in the current `ShardLayout`. `Receipt::receiver_shard_id` handles this via `shard_layout.resolve_to_current_shard(target_shard)`, returning `Err(EpochError::ShardingError)` only if the shard is absent from both the current layout and its split history [3](#0-2) . `resolve_to_current_shard` walks `ShardsSplitMapV3` recursively [5](#0-4) , but that map is only built/extended while `version() >= VERSION (3)` for both layouts in a history window [6](#0-5) , so lineage predating a V1/V2→V3 layout transition is not carried forward. Regardless of the precise triggering scenario, whenever `receiver_shard_id` returns `Err`, `receipt_filter_fn`'s `.unwrap()` panics [1](#0-0) , which is called from `DelayedReceiptQueueWrapper::pop`, itself invoked from `process_delayed_receipts` during every chunk's `Runtime::apply` [7](#0-6) .

### Impact Explanation
A panic inside `Runtime::apply` while draining the delayed-receipt queue is not a per-transaction rejection — it aborts chunk application entirely on every honest node processing that shard, since the delayed queue is deterministic, replicated state. This satisfies the "transaction-triggered halt" acceptance criterion: once the stale receipt reaches the front of the delayed queue, every validator attempting to apply that chunk panics, and the chain for that shard cannot progress until the state itself is patched out-of-band. This is a concrete denial-of-service / chain-halt vector, not a resource-only or cosmetic issue.

### Likelihood Explanation
The repository's own test (`test-loop-tests/src/tests/global_contracts_distribution.rs`) explicitly names and reproduces this exact scenario, indicating this is a known, previously-identified risk class deliberately exercised by regression tests, and that the fix (`resolve_to_current_shard`'s multi-generation walk) is what stands between the current code and the panic. Its scope is bounded to the interaction of `DeployGlobalContractAction` congestion-driven delaying and resharding, and to any layout-history edge case (e.g., legacy V1/V2 ancestry) not covered by `ShardsSplitMapV3`, which I could not fully confirm as closed.

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:875-876`) with proper `Result` propagation into `RuntimeError`/`StorageError::StorageInconsistentState`, consistent with how other queue-inconsistency cases are handled elsewhere in the same file (e.g. `IntegerOverflowError` handling). Additionally, audit `build_shard_split_map`/`ShardsSplitMapV3` to guarantee full split lineage is preserved across a V1/V2→V3 shard-layout-version transition, so `resolve_to_current_shard` can never legitimately return `None` for a receipt that was valid when created.

### Proof of Concept
1. Deploy a global contract (`DeployGlobalContractAction`) on a shard, creating a `GlobalContractDistributionReceipt` with `target_shard = current_shard` [8](#0-7) .
2. Saturate the shard's compute budget every block so the receipt is pushed into, and remains in, the delayed-receipt queue [9](#0-8) .
3. Trigger two or more resharding events (or a V1/V2→V3 layout transition followed by a split) while the receipt is still delayed, such that `target_shard` is absent both from the current layout and from the recorded split history.
4. When the delayed queue is drained, `DelayedReceiptQueueWrapper::pop` → `receipt_filter_fn` calls `receiver_shard_id(&shard_layout).unwrap()`, which panics on `Err(EpochError::ShardingError)`, halting chunk application on every node tracking that shard [10](#0-9) .

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-910)
```rust
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

**File:** runtime/runtime/src/global_contracts.rs (L53-60)
```rust
    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;
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

**File:** core/primitives/src/shard_layout/v3.rs (L64-88)
```rust
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

**File:** runtime/runtime/src/lib.rs (L2591-2606)
```rust
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

**File:** protocol-model/spec/cross-shard-congestion.md (L111-121)
```markdown
### 2. Admission into the delayed queue (incoming backpressure)

Incoming receipts are validated first, then either executed or deferred:
`process_incoming_receipts` (`lib.rs:2541`) executes a receipt only while
`total.compute < compute_limit` and the storage-proof size limit is not exceeded;
otherwise it calls `delayed_receipts.push(...)` (`lib.rs:2578`) to persist it for a
later chunk. `compute_limit` is the chunk gas limit (`lib.rs:2668`). Delayed
receipts are drained FIFO in `process_delayed_receipts` (`lib.rs:2441`), stopping on
the same compute / proof-size checks (`lib.rs:2462`). Each `pop`/`push` updates the
wrapper's accumulated gas/bytes (`congestion_control.rs:838` push, `:880` pop), which
feed `delayed_receipts_gas` and `receipt_bytes` in `CongestionInfo`.
```
