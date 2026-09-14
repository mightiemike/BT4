### Title
Unchecked `Receipt::receiver_shard_id` error causes chunk-apply panic on delayed `GlobalContractDistribution` receipts — ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unwraps the `Result` returned by `Receipt::receiver_shard_id()` without handling the error case, exactly the bug class described in CVE-2024-56727 (an unchecked error result from a lookup call leading to a crash). A `GlobalContractDistribution` receipt whose `target_shard` cannot be resolved to a shard in the current layout makes `receiver_shard_id()` return `Err(EpochError::ShardingError(..))`, and the `.unwrap()` at the call site turns that into an unconditional panic during delayed-receipt processing — a transaction-triggered halt reachable by any account that deploys a global contract.

### Finding Description
`Receipt::receiver_shard_id` is documented to fail when a `GlobalContractDistribution` receipt's `target_shard` is not present in the current shard layout and cannot be mapped forward via `resolve_to_current_shard`: [1](#0-0) 

`resolve_to_current_shard` only walks the *full* split history when the current layout is `ShardLayoutV3`; for legacy `V0`/`V1`/`V2` layouts it falls back to a single-generation lookup (`get_children_shards_ids`), so a receipt delayed across two or more resharding events under a non-V3 layout cannot be resolved and the call returns `Err`: [2](#0-1) 

The only caller that consumes this `Result` inside the runtime's delayed-receipt handling does not check it — it unwraps directly: [3](#0-2) 

`receipt_filter_fn` is invoked from `DelayedReceiptQueueWrapper::pop`, which drains the delayed-receipt queue on every chunk that has capacity, and from `peek_iter`: [4](#0-3) 

`pop` is called from `Runtime::process_delayed_receipts` on essentially every chunk, so once an unresolvable receipt reaches the head of the delayed queue, every node that applies that chunk hits the same `.unwrap()` panic deterministically (all honest nodes fail identically, which is why it manifests as a chain halt rather than a fork).

A regression test in the same tree demonstrates a variant of exactly this failure mode (two resharding generations, `GlobalContractDistribution` receipt sitting in the delayed queue, then draining after both splits complete) and explicitly documents that the risk is a panic inside `receipt_filter_fn`: [5](#0-4) 

That test currently passes for the case handled by `ShardLayoutV3::resolve_to_current_shard`'s recursive split-map walk, but the guard added there (`shards_split_map` lookup) only covers V3 layouts; it does not change the fact that `receipt_filter_fn` still calls `.unwrap()` on a function whose contract is "returns `Err` when the shard cannot be resolved," so any situation that still produces that `Err` (e.g., legacy `V1`/`V2` layout with multi-generation delay, or any future edge case in the V3 split-history bookkeeping) turns into an unhandled panic instead of a propagated `RuntimeError`.

### Impact Explanation
`.unwrap()` panicking inside chunk application is not a soft failure — it aborts the node process (or the async task applying the chunk) for every honest validator/chunk-producer tracking that shard, deterministically, on the same input. This is a transaction/receipt-triggered halt of chain progress for the affected shard, satisfying the "transaction-triggered halt" impact bar. It is reachable purely by submitting a `DeployGlobalContract`/global-contract-distribution transaction (contract-deployer persona) whose resulting receipt gets delayed across shard-layout changes — no validator or network-level compromise is required.

### Likelihood Explanation
Reaching the exact `Err` branch requires the target shard from an old `GlobalContractDistribution` receipt to become unresolvable in the current layout — currently guarded for `ShardLayoutV3` by the recursive `shards_split_map` walk, but still open for legacy `V0`/`V1`/`V2` layouts (single-generation `get_children_shards_ids` fallback) and for any future gap in split-history bookkeeping. The underlying pattern — a `Result`-returning shard-resolution API consumed with `.unwrap()` in the hot delayed-receipt path rather than propagated as `RuntimeError` — is the root defect; whether it is exploitable today depends on which shard-layout version and resharding history a given deployment is running, which makes likelihood conditional rather than certain, but the code path itself provides no error handling at all.

### Recommendation
Change `receipt_filter_fn` (and any other direct callers of `receiver_shard_id`/`shard_layout` in `DelayedReceiptQueueWrapper`) to propagate the `Result` instead of unwrapping, converting it to `RuntimeError::StorageError(StorageError::StorageInconsistentState(..))` (consistent with how sibling inconsistent-state conditions are already handled elsewhere in this file) so an unresolvable receipt fails the chunk gracefully instead of panicking the process. Since `receipt_filter_fn` is used inside `Iterator::filter` closures, this likely requires refactoring `pop`/`peek_iter` to use `try_fold`/manual loops that can short-circuit on `Result::Err`.

### Proof of Concept
1. Start (or roll a test harness) on a shard layout version that is still `V1`/`V2` (pre-dynamic-resharding), i.e., where `resolve_to_current_shard` falls back to the single-generation `get_children_shards_ids`.
2. As any account, submit `DeployGlobalContract` so a `GlobalContractDistribution` receipt is created and is sent toward the delayed-receipt queue of some shard (achievable by keeping the destination shard congested so the receipt cannot be admitted immediately).
3. Trigger two consecutive shard-layout changes (resharding events) while the receipt remains parked in the delayed queue, so its `target_shard` is now two split-generations old relative to the current layout.
4. Let congestion clear so `Runtime::process_delayed_receipts` pops the receipt: `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()`; since the legacy-layout `resolve_to_current_shard` cannot walk two generations, `receiver_shard_id` returns `Err`, and the `.unwrap()` panics, aborting chunk application on every node that processes that chunk.

### Citations

**File:** core/primitives/src/receipt.rs (L447-466)
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

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
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
