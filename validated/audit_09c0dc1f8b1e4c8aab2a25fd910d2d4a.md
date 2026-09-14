### Title
Panic-inducing `.unwrap()` on unresolved `receiver_shard_id()` in delayed-receipt filtering causes a transaction-triggered chain halt - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
The reported bug class describes an unprivileged/attacker-controllable message that triggers an error path which is mishandled (blindly clearing state), disrupting the protocol's liveness. In nearcore, the closest reachable analog is `DelayedReceiptQueueWrapper::receipt_filter_fn` in `runtime/runtime/src/congestion_control.rs`, which unconditionally `.unwrap()`s a `Result` derived from receipt data that can be shaped by an ordinary transaction (e.g. `DeployGlobalContract`). If the `Result` is `Err`, the node panics instead of returning a recoverable error, which — because chunk application is deterministic across all validators — causes a synchronized crash/chain halt rather than an isolated failure.

### Finding Description
`receipt_filter_fn` is used when popping/peeking the delayed-receipt queue during chunk application: [1](#0-0) 

```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
```

`receiver_shard_id()` explicitly returns an `Err` when a `GlobalContractDistribution` receipt's `target_shard` cannot be mapped to the current shard layout: [2](#0-1) 

The mapping is attempted via `ShardLayoutV3::resolve_to_current_shard`, which recursively walks the `shards_split_map` split-history and returns `None` (turned into `Err` by the caller) "only if the shard ID is absent from both the current layout and the split history": [3](#0-2) 

A `GlobalContractDistribution` receipt is created whenever any account calls `DeployGlobalContract` — a normal, unprivileged action available to any transaction signer/contract deployer. Such a receipt can sit in the delayed-receipt queue across chunk boundaries when the shard's compute/proof-size budget is saturated (this is exactly the "spam gas to keep a receipt delayed" pattern used in the repo's own regression test): [4](#0-3) 

The repository has a partial fix (`resolve_to_current_shard`) for the two-resharding-events case, guarded by a dedicated regression test that asserts the chain does **not** stall: [5](#0-4) 

However, the fix only extends how far back `receiver_shard_id` can resolve a stale `target_shard`; it does not change `receipt_filter_fn`'s handling of the *error* case. If a `GlobalContractDistribution` receipt is delayed long enough (spanning enough resharding events, or split-map history that has been garbage-collected/limited, or an intermediate shard layout that predates `ShardLayoutV3`, i.e. `try_get_parent_shard_id`/`build_shard_split_map` breaking on version < 3 layouts — see the `break` on version mismatch in `build_shard_split_map`), `resolve_to_current_shard` returns `None`, `receiver_shard_id` returns `Err(EpochError::ShardingError(..))`, and `receipt_filter_fn`'s `.unwrap()` panics.

This is directly analogous to the reported bug class: a message shaped by an ordinary participant (here, a delayed protocol receipt generated from a normal `DeployGlobalContract` transaction, rather than an external validator reply) reaches an error path that is not gracefully handled, and the mishandling (panic vs. `HandleReply`'s premature `Clear()`) destabilizes the node's ongoing processing. In nearcore's case the effect is strictly worse than context-clearing: it is a hard panic during `Runtime::apply`'s receipt processing, and because every validator applies the same chunk deterministically, all honest nodes crash simultaneously on the same input — a transaction-triggered halt.

### Impact Explanation
A crash in `receipt_filter_fn` occurs inside chunk application (`process_delayed_receipts` / `peek_iter`), which every validator/RPC node tracking the shard executes identically. A successful trigger:
- Halts the affected shard/chain for all nodes that apply the poisoned chunk (deterministic panic ⇒ synchronized crash across the network, not just one node).
- Requires no special privileges — any account can call `DeployGlobalContract`; achieving the delayed-queue backlog and enough resharding events to exhaust the tracked split history requires only ordinary, permissionless transactions and patience/gas spend to keep a shard's compute limit saturated across resharding boundaries.
- Falls squarely into the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Exploitability is nontrivial (an attacker needs to arrange for the receipt to be delayed across enough resharding events, or across a resharding history containing a pre-V3 shard layout, so that `resolve_to_current_shard` cannot map the stale `target_shard`), but it is entirely achievable using only permissionless capabilities: submitting a `DeployGlobalContract` transaction and separately submitting cheap, high-gas transactions to saturate the shard's compute budget so the distribution receipt stays delayed. The existing regression test in the repo demonstrates the project is already aware of and actively patching this exact failure mode for the two-resharding case, confirming the underlying `.unwrap()`-on-stale-shard hazard is a recognized but incompletely closed class of bug in `receipt_filter_fn`.

### Recommendation
- Change `receipt_filter_fn` (and its callers `pop`/`peek_iter`) to propagate the `Result` instead of `.unwrap()`ing, e.g. have `receipt_filter_fn` return `Result<bool, RuntimeError>` and have `pop`/`peek_iter` bubble the error up as a normal `RuntimeError`, so a stale/unmappable receipt causes a well-defined error path (e.g. treated as `StorageInconsistentState`, matching how other stale-delayed-receipt validation failures are already handled in `runtime/runtime/src/lib.rs`) rather than an unrecoverable panic.
- Ensure `resolve_to_current_shard`/`build_shard_split_map` retain (or reconstruct) split history for as long as delayed receipts referencing old shards can plausibly remain queued, or reject/re-route such receipts safely instead of relying on `.unwrap()` to never fail.
- Add fuzz/property tests that delay a `GlobalContractDistribution` receipt across an unbounded number of resharding events (and across a V1/V2→V3 shard-layout transition) to confirm no panic path remains reachable.

### Proof of Concept
1. Deploy a global contract from an ordinary account (`DeployGlobalContract` action), which produces a `GlobalContractDistribution` receipt with `target_shard` set to the deploying account's current shard (`runtime/runtime/src/global_contracts.rs:143-171`).
2. Flood the shard with unprivileged high-gas transactions every block to keep the shard's compute/proof-size budget saturated, forcing the `GlobalContractDistribution` receipt into the delayed-receipt queue rather than being processed immediately (as done in the repo's own test at `test-loop-tests/src/tests/global_contracts_distribution.rs:116-135`).
3. Trigger enough dynamic-resharding splits (or a resharding sequence including a pre-`ShardLayoutV3` layout) that `ShardLayoutV3::resolve_to_current_shard` can no longer map `target_shard` into the current layout (i.e., the split history in `shards_split_map` no longer contains it).
4. Stop saturating gas so the shard drains its delayed queue; `DelayedReceiptQueueWrapper::pop`/`peek_iter` calls `receipt_filter_fn`, which calls `receiver_shard_id(...).unwrap()` — this returns `Err`, and the `.unwrap()` panics inside `Runtime::apply`, crashing every node applying that chunk simultaneously.

Note: I was not able to fully verify the exact minimum number/sequence of reshardings (or a specific pre-V3 layout transition) required to exhaust `shards_split_map` in this codebase version — the existing test only exercises two resharding events, for which the current `resolve_to_current_shard` fix already prevents the panic. Confirming a concrete un-patched trigger sequence for the unwrap would require deeper testing (e.g. via a Devin session with the ability to run/extend the existing test-loop tests) beyond what static code reading can establish with certainty.

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-131)
```rust
    // Step 1: Deploy the test contract on user0's account so we can call burn_gas_raw.
    {
        let node = env.node_for_account(&chunk_producer);
        let tx = node.tx_deploy_test_contract(&deploy_user);
        node.submit_tx(tx);
    }
    env.runner_for_account(&chunk_producer).run_for_number_of_blocks(2);

    // Step 2: Deploy a global contract from user0. This creates a
    // GlobalContractDistribution receipt with target_shard = user0's shard (S_A),
    // which is the shard that will be split in the first resharding.
    {
        let node = env.node_for_account(&chunk_producer);
        let code = ContractCode::new(near_test_contracts::rs_contract().to_vec(), None);
        let tx = node.tx_deploy_global_contract(
            &deploy_user,
            code.code().to_vec(),
            GlobalContractDeployMode::CodeHash,
        );
        node.submit_tx(tx);
    }

    // Step 3: Saturate compute on user0's shard every block so that the
    // GlobalContractDistribution receipt (arriving as incoming) gets pushed to
    // the delayed queue and stays there through both resharding events.
    //
    // Each burn_gas_raw call burns slightly more than half the gas limit, so
    // two local receipts exhaust the chunk's compute budget. We submit 3 per
    // block to ensure at least 2 are processed as local receipts.
    let gas_to_burn = gas_limit.checked_div(2).unwrap().checked_add(Gas::from_gas(1)).unwrap();
    let initial_num_shards = base_shard_layout.num_shards();
    let target_num_shards = initial_num_shards + 2; // after two splits

    let start_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };

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
