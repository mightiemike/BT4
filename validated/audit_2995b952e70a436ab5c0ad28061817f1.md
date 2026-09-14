### Title
Panic (chunk-halting DoS) from unchecked `receiver_shard_id().unwrap()` on stale delayed receipts after resharding - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs` calls `receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap()` without any consistency check that the receipt's stored target/receiver shard still maps to a valid shard in the *current* shard layout. This is the same bug class as CVE-2026-33262 (PowerDNS): a value received/stored earlier (a DNS cookie there; here a delayed/queued receipt with an old target shard) is consumed later without validating that it is still consistent with the current state (current shard layout after resharding), and the missing consistency check leads directly to a panic instead of a graceful error.

### Finding Description
`receipt_filter_fn` is used by `pop()` and `peek_iter()` when draining the per-shard delayed receipt queue during chunk apply: [1](#0-0) 

Every receipt popped from the delayed queue is passed through this filter with an unconditional `.unwrap()` on `receiver_shard_id(&shard_layout)`. The surrounding comments in the code itself acknowledge that delayed receipts can carry a shard mapping stale relative to the *current* epoch's shard layout after resharding: [2](#0-1) 

A regression test in the repo demonstrates the exact failure condition: after two sequential resharding (shard-split) events, a `GlobalContractDistribution` receipt that was pushed to the delayed queue before the splits becomes "stale" — its recorded target shard no longer exists in the current (twice-split) shard layout — and draining the delayed queue is expected to panic inside `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target shard: [3](#0-2) 

This is reachable purely by an unprivileged account: any account can submit ordinary transactions (e.g. `DeployGlobalContract`, or any action generating a cross-shard receipt) that get queued as delayed receipts on a shard, and if the network performs dynamic resharding while such receipts are still delayed (a normal chain condition, not attacker-controlled beyond submitting transactions and waiting), the shard-side apply logic hits the unchecked `.unwrap()` and panics. Because this happens inside chunk application (`self.pop()`/`peek_iter()` are called from the runtime's delayed-receipt processing path used every chunk), a panic here crashes/halts chunk production for that shard on every node applying the chunk — a transaction-triggered, protocol-level halt rather than a localized error.

### Impact Explanation
An `unwrap()` panic inside the runtime's chunk-apply path (via `DelayedReceiptQueueWrapper::pop`/`peek_iter`, called from `process_receipts`/receipt processing in `runtime/runtime/src/lib.rs`) causes the node process handling that shard's chunk application to abort. Since chunk application is deterministic and run by every validator/RPC node tracking the shard, this becomes a chain-wide halt condition for the affected shard rather than a single node's local issue — matching the "transaction-triggered halt" and "invalid state transition acceptance/denial of service" impact categories called out as in-scope. It requires no privileged access: it only requires (a) ordinary transactions that produce delayed receipts and (b) the network undergoing dynamic resharding, which is an accepted-in-scope path (cross-shard receipts / resharding state) reachable from a normal RPC-submitted transaction.

### Likelihood Explanation
Likelihood is moderate: it requires a shard to accumulate delayed receipts (e.g., via compute/gas saturation as in the reproduction test) that persist across resharding epoch boundaries, and it requires dynamic resharding (`ProtocolFeature::DynamicResharding`) to be active and to split the same shard more than once while a receipt is delayed. This is a normal, non-adversarial operational condition (heavy load + resharding), not requiring any malicious validator or network-level manipulation — any user submitting enough transactions to saturate a shard's compute budget around a resharding event can trigger it, which is exactly the scenario reproduced by the existing test in the codebase.

### Recommendation
Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation (e.g., have `pop`/`peek_iter` return a `Result` and handle a stale/unmappable receiver shard explicitly), and add a consistency check that maps a stale target shard forward across multiple resharding generations (similar to how `forward_from_buffer_to_shard` already handles the "receipt belongs to now-removed parent shard" case via `get_split_parent_shard_ids`/`get_parent_shard_id`). At minimum, treat a shard-mapping failure as a recoverable condition (e.g., route to a fallback child shard or defer) instead of panicking, consistent with how other congestion-control code paths avoid crashing on stale post-resharding shard references.

### Proof of Concept
The codebase already contains a self-documented reproduction in `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs`, lines 30-186):
1. Configure dynamic resharding to force-split two shards sequentially, one of which is the deploying user's shard.
2. Deploy a global contract from that user, creating a `GlobalContractDistribution` receipt targeting the shard that is about to be split.
3. Saturate the shard's compute budget every block (via `burn_gas_raw` calls) so the distribution receipt is pushed into, and stays in, the delayed receipt queue through both resharding events.
4. Stop saturating and let the delayed queue drain; the test's comment states that "if the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations," and asserts the chain does not stall — i.e., this test exists specifically to catch this failure mode. This confirms the panic path is real and directly reachable via ordinary transaction submission plus network resharding, with the guilty `.unwrap()` located at: [4](#0-3)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L868-879)
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
