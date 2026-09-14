### Title
Unhandled `.unwrap()` on `receiver_shard_id` in delayed-receipt filtering can panic honest nodes and halt the chain - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) unconditionally `.unwrap()`s the `Result` returned by `Receipt::receiver_shard_id`, which itself can legitimately return `Err(EpochError::ShardingError(..))` when a `GlobalContractDistribution` receipt's stored `target_shard` cannot be resolved to any shard in the current layout's split history. This mirrors the reported bug class exactly: an external/derived value (here, a stale cross-epoch shard mapping instead of a Chainlink price feed) is consumed via unchecked unwrapping instead of graceful fallback/error handling, so any failure of that lookup crashes the caller instead of being handled.

### Finding Description
`Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-466`) computes the shard a receipt belongs to. For ordinary receipts this is a plain `account_id_to_shard_id` lookup that cannot fail. But for `ReceiptEnum::GlobalContractDistribution`, the receipt carries a `target_shard` set when the receipt was created, which can reference a shard ID from an old shard layout. The function tries `shard_layout.resolve_to_current_shard(target_shard)` (`core/primitives/src/shard_layout/v3.rs:320-326`) and only returns `Err(EpochError::ShardingError(...))` if that historical shard ID is "absent from both the current layout and the split history" — i.e., `resolve_to_current_shard` returns `None`.

That `Result` is then consumed at `runtime/runtime/src/congestion_control.rs:876`:
```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
```
`receipt_filter_fn` is called from `pop()` (used while applying delayed receipts every chunk) and from `peek_iter()` (`congestion_control.rs:912-920`), both of which are on the hot path of chunk application (`runtime/runtime/src/lib.rs`), executed by every validator applying a shard. There is no error propagation path here — any `Err` becomes an immediate `panic!`.

A `test-loop-tests` regression test (`test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`, `test_stale_global_contract_distribution_after_double_resharding`) explicitly documents this exact scenario: a `GlobalContractDistribution` receipt is deliberately kept in the delayed queue across two resharding events so that its `target_shard` becomes stale, and the test comment states "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations." The fix that shipped (`resolve_to_current_shard` walking the full split history) narrows the window in which `receiver_shard_id` errors, but the `.unwrap()` in `receipt_filter_fn` remains: any receipt whose `target_shard` predates the recorded split history (e.g. it is not covered by `shards_split_map`, which per its own docstring is a "full history" only as far back as it has been carried forward — `core/primitives/src/shard_layout/v3.rs:10-19`) will still hit `Err` and panic.

### Impact Explanation
A panic inside `pop()`/`peek_iter()` during chunk application is a `RuntimeError`/unrecoverable panic that occurs identically on every honest validator applying that shard, because it is deterministic state-transition logic driven by on-chain, attacker-influenced data (the receipt's persisted `target_shard`). This satisfies the "transaction-triggered halt" criterion: a chunk that reaches this code path cannot be applied by any correctly-tracking validator, stalling chunk/block production for that shard network-wide until manual intervention (comparable in effect to the `near_resharding_status = Failed` manual-recovery scenario documented in `docs/architecture/how/resharding_v2.md`). This is a Medium/High-severity DoS class matching the "transaction-triggered halt" acceptance criterion in the validation rubric, directly analogous to the Chainlink report's core claim that an unhandled failure path in an external data lookup causes denial of service.

### Likelihood Explanation
Triggering requires an actor to deploy a `GlobalContractDistribution` receipt (any unprivileged account can deploy a global contract) and arrange — via congestion/compute saturation of the target shard, exactly as demonstrated in the existing regression test — for the receipt to sit in the delayed queue across resharding events until its `target_shard` falls out of the tracked split history. This depends on dynamic/V3 resharding being enabled and on the number of resharding generations exceeding what `shards_split_map` retains; it is not trivially triggerable on a static-layout chain, but is realistically reachable on any network that performs multiple dynamic reshardings while an attacker deliberately delays a `GlobalContractDistribution` receipt (a scenario the codebase's own test suite treats as a real, previously-exploitable condition).

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` (and any other caller of `receiver_shard_id` that currently unwraps) with explicit error propagation (`Result`/`RuntimeError`) so an unresolvable `target_shard` produces a graceful `InvalidTxError`/receipt-rejection or a well-defined fallback (e.g., treat as belonging to no shard and drop/GC) rather than a process panic. Additionally, consider bounding or permanently retaining split-history entries for shards holding delayed `GlobalContractDistribution` receipts so `resolve_to_current_shard` cannot legitimately return `None` for any receipt still live in the delayed queue.

### Proof of Concept
1. Deploy a chain with `ProtocolFeature::DynamicResharding` enabled and V3 shard layouts.
2. As an unprivileged account, submit a `DeployGlobalContract` transaction whose resulting `GlobalContractDistribution` receipt targets shard `S_A`.
3. Saturate compute on `S_A` every block (e.g., via repeated `burn_gas_raw` calls, as done in `call_burn_gas_contract`/the existing test helper) so the receipt is pushed into and stays in the delayed-receipt queue.
4. Force/await two (or more, exceeding the tracked split-history depth) resharding events that split `S_A`'s descendants further, so that `target_shard` becomes older than what `shards_split_map` retains.
5. Stop saturating and let the delayed queue drain; when the runtime pops/peeks the stale receipt, `Receipt::receiver_shard_id` returns `Err(EpochError::ShardingError)`, and `receipt_filter_fn`'s `.unwrap()` panics, halting chunk application for every honest node tracking that shard.

This scenario is already codified (for the two-resharding case that the shipped fix addresses) in [1](#0-0) , and the still-present unchecked unwrap is at [2](#0-1) , fed by the fallible resolution logic at [3](#0-2)  and [4](#0-3) .

### Citations

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

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

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
