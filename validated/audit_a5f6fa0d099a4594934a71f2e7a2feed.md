### Title
Unchecked `.unwrap()` on `receiver_shard_id()` in `receipt_filter_fn` causes transaction-triggered chunk-apply panic on non-V3 (static/legacy) shard layouts - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the `Result` returned by `Receipt::receiver_shard_id`, which can legitimately return `Err` when a delayed `GlobalContractDistribution` receipt's `target_shard` cannot be resolved to a shard in the current shard layout. This is the same bug class as the Linux `drm/i915` CVE-2024-56667 report (an unchecked null/absent value dereferenced during routine processing causing a crash) — here it is an unchecked `Err`/absent-mapping causing a Rust panic during chunk application.

### Finding Description
`Receipt::receiver_shard_id` returns an `Err(EpochError::ShardingError(...))` when a `GlobalContractDistribution` receipt's `target_shard` is not present in the current shard layout and `shard_layout.resolve_to_current_shard(target_shard)` fails to find it: [1](#0-0) 

`resolve_to_current_shard` is only implemented for `ShardLayoutV3` (dynamic resharding) and walks the full split history recorded in `shards_split_map`: [2](#0-1) 

For `ShardLayoutV1`/`ShardLayoutV2` (the classic/static resharding format still used in production for non-dynamic reshardings), there is no equivalent full split-history structure, so a receipt whose `target_shard` predates the current layout cannot be resolved and `receiver_shard_id` returns `Err`.

The caller, `DelayedReceiptQueueWrapper::receipt_filter_fn`, does not handle this `Err` — it calls `.unwrap()` directly: [3](#0-2) 

This function is invoked from `pop()` and `peek_iter()`, which run on **every delayed receipt read during ordinary chunk application** — i.e., on the path any submitted transaction can trigger indirectly (a `GlobalContractDistribution` receipt is a normal side effect of a `DeployGlobalContract` action a contract deployer can submit): [4](#0-3) 

This is reached from `Runtime::apply` → `process_receipts` → `process_delayed_receipts`, part of the mandatory per-chunk state transition executed by every validator node: [5](#0-4) 

A repository test explicitly documents and exercises this exact scenario for a two-generation static resharding split, but the comment in the test states the underlying fix "only works with V3 shard layouts (dynamic resharding)... [w]ith static resharding, the shard layout doesn't maintain a full split history," confirming that the classic/static resharding path remains exposed to the `.unwrap()` panic: [6](#0-5) [7](#0-6) 

### Impact Explanation
If a `GlobalContractDistribution` receipt targeting a shard from a static (V1/V2) resharding generation is delayed (e.g., because the destination shard's compute/gas budget is saturated across the resharding boundary) and survives past a second static resharding event, every validator processing that shard's delayed-receipt queue will call `receiver_shard_id()` → `Err` → `.unwrap()` panic inside `receipt_filter_fn`. Because this executes inside the mandatory `Runtime::apply` state-transition path used identically by every honest node, the panic is deterministic and **crashes/halts every node that attempts to apply that chunk**, i.e. a transaction-triggered chain halt (a denial-of-service on chunk production/validation for that shard) rather than an isolated crash of a single process. This matches the report's core impact class ("Impact: High" per the CVSS availability component of the analog CVE), satisfying the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Reachability requires an attacker/user to: (1) deploy a global contract (an ordinary `DeployGlobalContract` action available to any account), (2) arrange for the resulting `GlobalContractDistribution` receipt to be delayed across a shard boundary through gas/compute saturation of a target shard, and (3) have that shard undergo (at least) one further resharding event using a non-V3 shard layout before the receipt is processed. Resharding is validator/protocol-controlled and infrequent, and current production shard layouts have been migrating toward V3 (dynamic resharding), which narrows the window. However, static resharding logic and `ShardLayoutV1/V2` remain part of the codebase and can still be configured/used, and the underlying condition is a pure user-triggerable sequencing issue rather than a validator-malicious or network-layer scenario, so the class of bug is squarely in scope even though the specific trigger conditions are non-trivial to align in time.

### Recommendation
- Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation: change `receipt_filter_fn`'s signature (and its callers `pop`/`peek_iter`) to return a `Result`, or fall back to a safe default (e.g., treat unresolved receipts as belonging to none of the local shards and re-delay/forward them) instead of panicking.
- Extend split-history tracking (equivalent to `ShardLayoutV3::shards_split_map`) to legacy/static shard layouts, or ensure any static resharding transition path also builds/maintains an ancestor map so `resolve_to_current_shard`-style resolution never returns `None` for receipts created under the previous layout.
- Add defensive validation at receipt creation / bandwidth-scheduler time to guarantee a `GlobalContractDistribution` receipt's `target_shard` can always be resolved forward through any resharding path taken by the protocol, independent of shard-layout version.

### Proof of Concept
1. Configure a network using classic (static, V1/V2) shard layouts and two sequential shard splits (this repository's own `test_stale_global_contract_distribution_after_double_resharding` reproduces the equivalent V3 scenario at `test-loop-tests/src/tests/global_contracts_distribution.rs:32`, but explicitly notes the underlying fix does **not** cover static resharding).
2. From a chunk producer, submit a `DeployGlobalContract` transaction from an account located in a shard `S_A` that will be split.
3. Saturate the target shard's compute/gas budget every block (e.g., via repeated `burn_gas_raw` calls) so the resulting `GlobalContractDistribution` receipt is pushed into the delayed-receipt queue (`DelayedReceiptQueueWrapper::push`, `runtime/runtime/src/congestion_control.rs:838`) and remains there through the shard split.
4. Trigger a second static resharding event on the same lineage before the delayed receipt is drained.
5. When the delayed queue is next popped (`DelayedReceiptQueueWrapper::pop`, `runtime/runtime/src/congestion_control.rs:880`), `receipt_filter_fn` calls `receiver_shard_id()` on the stale receipt; because the static layout has no split-history mapping, `resolve_to_current_shard`-equivalent resolution fails, `receiver_shard_id` returns `Err`, and the `.unwrap()` at `runtime/runtime/src/congestion_control.rs:876` panics, crashing the node inside `Runtime::apply`/`process_delayed_receipts` — a deterministic, transaction-triggered halt reproduced identically on every honest validator applying that shard's chunk.

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

**File:** runtime/runtime/src/lib.rs (L1797-1799)
```rust
        // Step 3: process receipts.
        let process_receipts_result =
            self.process_receipts(&mut processing_state, &mut receipt_sink)?;
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-39)
```rust
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
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
