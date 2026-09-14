### Title
Panic-inducing `unwrap()` in delayed-receipt processing when a `GlobalContractDistribution` receipt's `target_shard` cannot be remapped after resharding — ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::pop`'s `receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs:868-878` calls `.unwrap()` on `receipt.get_receipt().receiver_shard_id(&shard_layout)` for every receipt drained from the delayed-receipt queue. If a `GlobalContractDistribution` receipt (created from a user-submitted `DeployGlobalContract` action, see `initiate_distribution` / `apply_global_contract_distribution_receipt` in `runtime/runtime/src/global_contracts.rs:111-333`) sits in the delayed queue long enough that the shard-layout history can no longer resolve its stale `target_shard` back to a live shard, `receiver_shard_id()` returns an `Err`, and the bare `.unwrap()` panics inside chunk application — a node crash on every validator applying that chunk, i.e. a deterministic, transaction-triggered halt.

### Finding Description
This mirrors the CVE-2019-19308 bug class: a code path assumes a derived/looked-up value is always present (the GNOME font viewer assumed `g_strconcat` always returns non-NULL) and unconditionally dereferences/unwraps it. Here, `receipt_shard_id` in `runtime/runtime/src/congestion_control.rs:876` is computed from `receiver_shard_id(&shard_layout)` and immediately `.unwrap()`-ed with no fallback:

```
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
``` [1](#0-0) 

`GlobalContractDistributionReceipt::target_shard()` (`core/primitives/src/receipt.rs:928-933`) is a `ShardId` captured at the time the distribution receipt was created (`initiate_distribution`, `runtime/runtime/src/global_contracts.rs:143-171`) and forwarded/updated shard-by-shard in `forward_distribution_next_shard` (`runtime/runtime/src/global_contracts.rs:288-333`). While a receipt is buffered in the delayed queue across resharding epochs (e.g., because the target shard's compute budget is saturated, forcing `process_incoming_receipts`/`process_delayed_receipts` to push it via `delayed_receipts.push`, `runtime/runtime/src/lib.rs:2707-2711`), the shard layout can undergo multiple resharding events. `receiver_shard_id` must remap the old `target_shard` through the shard-layout split history to the currently valid shard id; the code comment and a dedicated regression test explicitly document that this remap can fail after two sequential resharding generations:

```
// If the vulnerability exists, processing the stale GlobalContractDistribution
// receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
// to remap the old target_shard after two resharding generations.
``` [2](#0-1) 

The test `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:30-186`) constructs exactly this scenario: a normal account deploys a global contract (`DeployGlobalContract`), the resulting distribution receipt's target shard is force-split twice via dynamic resharding, and the test saturates gas on the target shard so the receipt is pushed into the delayed queue and survives both splits. The test only checks that the chain keeps making progress past the point where the panic would occur; it does not independently prove that `receiver_shard_id` always succeeds for every possible resharding topology (number of consecutive splits, split-history retention limits, or interaction with `min_epochs_between_resharding`/GC of layout history) — the surrounding `pop()` code (`congestion_control.rs:880-909`) still contains no `Err` handling for this call, only the outer `.unwrap()`.

### Impact Explanation
An unhandled panic inside `apply_chunk`/`process_delayed_receipts` on a runtime code path that every validator executes deterministically for the same chunk means the panic occurs on all honest nodes simultaneously — this is a **transaction-triggered halt**: any account holder who deploys a global contract (`DeployGlobalContract`, reachable by any signer) can create a receipt whose `target_shard` becomes stale relative to the shard layout after sufficient resharding activity elapses while the receipt is delayed. If the remap ever fails (e.g., beyond the split-history depth the fix currently covers, or in configurations/topologies not exercised by the single regression test), the resulting `.unwrap()` panic crashes the whole network's block production for that shard, since every node applying the chunk hits the same code path and input.

### Likelihood Explanation
Exploitation requires: (1) submitting a `DeployGlobalContract` transaction — trivially available to any unprivileged account; (2) the resulting distribution receipt being delayed (achievable by saturating the target shard's chunk gas/compute budget, itself achievable by a submitter with enough gas-purchasing capability); and (3) enough resharding activity occurring while the receipt is delayed to exceed however much split history `receiver_shard_id` can resolve. Item (3) is normally outside attacker control (resharding cadence is protocol/ops-controlled), which reduces likelihood on networks with static or infrequent resharding, but on any network using `DynamicResharding` (protocol feature present in this codebase) with multiple splits during the retention window, this is directly triggerable by an unprivileged actor with no special permissions beyond normal transaction submission and enough funds to pay gas.

### Recommendation
Replace the bare `.unwrap()` calls in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:876`) and the epoch-info `shard_layout(...).unwrap()` (`:875`) with explicit error propagation (`Result`) so a failed remap surfaces as a recoverable `RuntimeError`/`StorageInconsistentState` rather than an unconditional panic, matching the pattern already used elsewhere in this file (e.g. `checked_add(...).ok_or(IntegerOverflowError)`). Additionally, verify and, if necessary, extend the shard-layout split-history retention so `receiver_shard_id` can always resolve any `target_shard` a `GlobalContractDistributionReceipt` could carry for as long as it can remain in the delayed queue, and add coverage for more than two sequential resharding generations to close any gap the current single regression test does not exercise.

### Proof of Concept
1. Deploy a global contract from an ordinary account via `DeployGlobalContract` (any signer, no special permission) — creates a `GlobalContractDistributionReceipt` with `target_shard` = the deployer's current shard (`runtime/runtime/src/global_contracts.rs:143-171`).
2. Saturate the target shard's per-chunk compute/gas budget every block (e.g., repeated `FunctionCall`s burning gas) so the distribution receipt is pushed into, and remains in, the delayed-receipt queue (`runtime/runtime/src/lib.rs:2707-2711`).
3. While the receipt sits delayed, drive the network through two (or more, depending on retained split history) resharding events that split the shard containing the receipt's stale `target_shard`.
4. Once compute pressure is released and the delayed queue drains, `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id(&shard_layout).unwrap()` on the stale receipt; if the split history can no longer remap `target_shard`, this panics inside `apply_chunk`, halting chunk production for that shard on every node.
   - This exact sequence is codified in `test-loop-tests/src/tests/global_contracts_distribution.rs::test_stale_global_contract_distribution_after_double_resharding` (lines 30-186), which explicitly documents the panic path in its comments; note the test currently asserts the chain does *not* stall, so whether the underlying fix (shard-layout split-history depth) fully closes the gap for all resharding topologies is **unverified** from the available code — the `.unwrap()` calls themselves remain unguarded in `congestion_control.rs`.

**Uncertainty note:** I could not locate the implementation of `receiver_shard_id` (`core/primitives/src/receipt.rs`) within the indexed context to confirm definitively whether it can still return `Err` for topologies beyond the two-split case covered by the regression test, or whether a separate upstream fix (e.g., unbounded split-history retention) makes the `Err` branch unreachable in practice. The `.unwrap()` calls in `congestion_control.rs:875-876` are confirmed present in the indexed source; a Devin session with full repository access would be needed to inspect `receiver_shard_id`'s current guarantees and any related split-history retention limits before treating this as conclusively exploitable versus already mitigated.

### Citations

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-187)
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
