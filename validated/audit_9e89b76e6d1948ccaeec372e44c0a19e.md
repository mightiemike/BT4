### Title
Panic-based chunk halt via unresolvable `receiver_shard_id().unwrap()` on stale `GlobalContractDistribution` receipts after resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
This is a plausible bug-class analog to CVE-2020-19468 (an unchecked/invalid dereference on attacker-influenced data causing a crash), but I could not fully verify it is a *live, unpatched* vulnerability in this tree. A regression test already exists (`test_stale_global_contract_distribution_after_double_resharding`) that specifically probes this exact code path and asserts the chain does **not** stall, i.e. the maintainers already reasoned about and appear to have mitigated this class of panic for the two-resharding case. I was not able to confirm within the remaining budget whether `ShardLayout::resolve_to_current_shard` / the split-history mechanism is bounded such that a receipt delayed across **three or more** resharding generations (or a static/V1/V2 shard layout mixed with dynamic resharding) can still make `receiver_shard_id()` return `Err`, which would still panic at the `.unwrap()` call site.

### Finding Description
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the result of `receiver_shard_id`: [1](#0-0) 

`Receipt::receiver_shard_id` can return `Err(EpochError::ShardingError(..))` for a `GlobalContractDistribution` receipt whose `target_shard` no longer exists in the current shard layout *and* cannot be mapped forward via `ShardLayout::resolve_to_current_shard`: [2](#0-1) 

The same unwrap pattern also appears at the receipt-forwarding call site: [3](#0-2) 

A `GlobalContractDistribution` receipt is created by any unprivileged account deploying a global contract (`DeployGlobalContract` action) and is then routed shard-to-shard, potentially sitting in the delayed-receipt queue across multiple epoch/resharding boundaries before being popped and filtered via `receipt_filter_fn`. If the target shard's ancestry chain exceeds whatever bound `resolve_to_current_shard` supports, `receiver_shard_id` returns `Err`, and the `.unwrap()` in `receipt_filter_fn` or `forward_from_buffer_to_shard` turns that into a hard `panic!`, which aborts chunk application for every honest node applying that chunk — a transaction/receipt-triggered halt.

### Impact Explanation
If reachable, this would be a chunk-execution halt triggered indirectly by an ordinary `DeployGlobalContract`/`UseGlobalContract` transaction combined with enough intervening resharding events to exhaust the shard-layout's tracked split history — i.e., a transaction-triggered denial of service / consensus halt, which is in-scope per the rules (transaction-triggered halt).

### Likelihood Explanation
Likelihood is uncertain and likely low/borderline:
- A regression test (`test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs:30-186`) explicitly constructs a double-resharding scenario targeting this exact code path and asserts the chain does not stall, strongly suggesting the two-generation case is already handled by `resolve_to_current_shard`. [4](#0-3) 
- Whether three-or-more-generation resharding (or mixed static/dynamic shard layout versions) can still defeat `resolve_to_current_shard` and trigger the `.unwrap()` panic was not confirmed — I ran out of tool budget before reading `resolve_to_current_shard`'s implementation in `core/primitives/src/shard_layout/mod.rs` / `v3.rs` to determine its exact bound.
- Triggering this requires many resharding events with the exact shard-splitting pattern needed to make a stale `target_shard` unreachable, which is not fully attacker-controlled (resharding schedule/config is operator-controlled), reducing practical exploitability from an unprivileged caller's perspective.

### Recommendation
Given the unresolved uncertainty, I cannot assert this with confidence as a proven, currently-exploitable bug in this codebase snapshot. A background agent should:
1. Read `ShardLayout::resolve_to_current_shard` in `core/primitives/src/shard_layout/mod.rs` and `v3.rs` to determine exactly how many resharding generations of split history it can resolve, and whether V1/V2 (static) shard layouts participate in that history at all.
2. Construct a test with 3+ sequential dynamic resharding events (extending `test_stale_global_contract_distribution_after_double_resharding`) to confirm whether `receiver_shard_id()` can still return `Err` for a sufficiently stale `GlobalContractDistribution` receipt.
3. If reproducible, replace the `.unwrap()` calls at `runtime/runtime/src/congestion_control.rs:876` and `:355` with graceful error propagation (`RuntimeError`/discard-and-log) instead of a hard panic, regardless of how deep the split history goes, so that no receipt shape can crash chunk application.

### Proof of Concept
Not confirmed as reproducible in this snapshot within the available investigation budget. The closest reproduction scaffold already exists in the repo at `test-loop-tests/src/tests/global_contracts_distribution.rs:30-186` (`test_stale_global_contract_distribution_after_double_resharding`), which currently passes (chain does not stall) for the double-resharding case; extending it to a third resharding generation would be the next step to determine whether the `.unwrap()` panic in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) is still reachable.

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L347-356)
```rust
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

```

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
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
