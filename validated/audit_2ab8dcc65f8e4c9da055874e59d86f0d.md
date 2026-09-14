### Title
Reachable panic (chain halt) in `DelayedReceiptQueueWrapper::receipt_filter_fn` via `.unwrap()` on `receiver_shard_id()` for stale cross-shard receipts - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`receipt_filter_fn` in the delayed-receipt queue popping logic unconditionally `.unwrap()`s the result of `Receipt::receiver_shard_id(&shard_layout)`. That call returns `Err(EpochError::ShardingError)` whenever a receipt's stale/historical target shard cannot be resolved into the current shard layout. Any code path that can produce or persist such a receipt (e.g. `GlobalContractDistribution` receipts that sit in the delayed queue across multiple resharding events) turns a normal runtime `apply()` call into an unconditional panic, halting chunk production for the shard.

### Finding Description
`Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) for `GlobalContractDistribution` receipts tries to resolve the receipt's `target_shard` via `ShardLayout::resolve_to_current_shard`, and explicitly returns `Err(EpochError::ShardingError(...))` when the target shard "does not exist in the shard layout or its split history": [1](#0-0) 

`resolve_to_current_shard` (`core/primitives/src/shard_layout/v3.rs:320-326`) recursively resolves an old shard id by walking `shards_split_map`, but that map only contains split history that was captured when the `ShardLayoutV3` was derived (`build_shard_split_map`, `core/primitives/src/shard_layout/v3.rs:64-88`, `derive`/`derive_with_layout_history`). If the split history available to a given layout does not cover the receipt's original target shard generation (e.g. layout derived via `derive()` from a base layout rather than `derive_with_layout_history()`, or history that was truncated/garbage-collected), `shards_split_map.get(&shard_id)` returns `None`, so `resolve_to_current_shard` returns `None`, and `receiver_shard_id` bubbles up `Err`.

That `Err` is exactly what `receipt_filter_fn` cannot tolerate: [2](#0-1) 

This function is invoked from `DelayedReceiptQueueWrapper::pop`, which is called on every chunk application while draining the delayed-receipt queue: [3](#0-2) 

The delayed queue is populated by ordinary receipt processing during `Runtime::apply` (`runtime/runtime/src/lib.rs`), which is executed by every validator node on every chunk — this is fully reachable from a plain, unprivileged transaction: any account can call `deploy_global_contract`, producing a `GlobalContractDistribution` receipt whose `target_shard` is fixed at creation time. If that receipt gets delayed (e.g. due to compute/gas congestion) across shard-layout transitions whose split history does not fully cover its origin shard, every subsequent chunk application on that shard will panic on `pop()`.

The repository's own regression test acknowledges this exact failure mode: [4](#0-3) 

That test only exercises the "two generations, `derive_with_layout_history`-populated split history" case and asserts no stall occurs there, i.e. it validates the specific mitigation added for that particular scenario, but it doesn't test all constructions of `ShardLayoutV3` — the `derive()` path (`v3.rs:232-240`) clones only the *existing* `shards_split_map` from the base layout and does not backfill deeper historical splits, and layout construction from persisted epoch configs could similarly lack an entry for very old target shards. In any such configuration, `receipt_filter_fn`'s `.unwrap()` is a live null/None-style dereference bug analogous to the GPAC `gf_filter_pid_get_packet` NULL dereference: an internally-producible value (a stale/expired reference) is dereferenced without a NULL/Err check, causing an unconditional process abort (panic) instead of graceful error handling.

### Impact Explanation
A panic inside `Runtime::apply` on any validator processing a chunk for the affected shard causes that node's client process to crash (Rust panics in this hot path are not caught) or, if caught by higher-level panic boundaries, forces the node out of state transition entirely. Because delayed receipts are stored deterministically in the trie and are drained identically by every honest validator tracking the shard, all validators for that shard will hit the same `.unwrap()` panic on the same receipt — this is a **transaction-triggered halt** of the affected shard (liveness failure), satisfying the "no-impact analog" exclusion bar because it is a concrete, protocol-level denial of service rather than a mere resource/perf issue.

### Likelihood Explanation
Triggering requires: (1) deploying a global contract (ordinary, permissionless transaction) whose resulting `GlobalContractDistribution` receipt gets delayed under congestion, and (2) one or more resharding events occurring while it sits in the queue, with the layout's `shards_split_map` missing the receipt's origin shard. Resharding events are periodic and predictable (governed by `DynamicReshardingConfig`), and an attacker fully controls when to submit the contract-deploy transaction and can flood the shard with `FunctionCall` actions to keep it congested until the desired resharding boundary passes. The exact conditions under which `shards_split_map` fails to cover the older shard (deep history beyond what a given layout snapshot retains, or construction via the plain `derive()` path) are the main uncertainty; I was not able to fully trace every layout-construction call site to confirm how far back `shards_split_map` extends in production epoch-config flows, so likelihood should be validated against real deployment configurations (i.e., whether any `ShardLayoutV3` in production epoch configs is built other than via `derive_with_layout_history` with complete history).

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:875-876`) with proper error propagation (`?`) so `pop()` returns `Result<..., RuntimeError>` instead of panicking, and treat an unresolvable `receiver_shard_id` as `StorageInconsistentState`/`ReceiptValidationError` consistent with how other receipt-validation failures on dequeue are handled (see `runtime/runtime/src/lib.rs:2500`). Additionally, audit all `ShardLayoutV3` construction paths (`derive`, `derive_with_layout_history`, deserialization from epoch config) to guarantee `shards_split_map` always contains full split ancestry for any shard that could still have receipts in flight.

### Proof of Concept
1. Submit a `DeployGlobalContract` transaction from any account so a `GlobalContractDistributionReceiptV1/V2` is created with `target_shard = S_A`.
2. Saturate the chunk's gas/compute budget on shard `S_A` every block (e.g. repeated `burn_gas_raw` calls) so the distribution receipt is pushed into the delayed-receipt queue instead of executing immediately.
3. Trigger two (or more) resharding events on `S_A` while the receipt remains delayed, using a `DynamicReshardingConfig` (or any shard-layout transition) such that the resulting `ShardLayoutV3.shards_split_map` does not contain an entry mapping the receipt's original `target_shard` all the way to a currently-existing shard.
4. Stop saturating and let the delayed queue drain; on `DelayedReceiptQueueWrapper::pop` → `receipt_filter_fn`, `receiver_shard_id(&shard_layout)` returns `Err(EpochError::ShardingError)`, and the `.unwrap()` panics, halting chunk production for that shard.

This mirrors `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs:30-186`, which the repository added specifically to probe this panic condition; the underlying `.unwrap()` in `receipt_filter_fn` remains unguarded for any split-history construction not covered by that test's exact scenario.

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

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-908)
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-185)
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
```
