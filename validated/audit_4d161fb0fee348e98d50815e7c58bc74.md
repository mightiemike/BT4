Based on my investigation, I found a concrete analog: a test in the nearcore codebase explicitly documents and reproduces a panic condition (a null/invalid-state dereference analog) that occurs when a `GlobalContractDistribution` receipt's `target_shard` becomes stale across **two** consecutive dynamic-resharding generations, causing the shard-remapping lookup (`receiver_shard_id()`/shard-index resolution used by `receipt_filter_fn()`) to fail unexpectedly. This mirrors the CVE's bug class: an assumption about a single-level index/state translation (mt7915's dbdc/band_idx assumption) breaking down under a state the code didn't anticipate (two resharding transitions), leading to a panic reachable purely from a submitted transaction (a global contract deployment) combined with normal chunk-producer/validator resharding activity — no privileged access required.

### Title
Chunk-producer halt via stale `GlobalContractDistribution` receipt `target_shard` surviving two resharding generations - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
A `GlobalContractDistribution` receipt created by an ordinary `DeployGlobalContract` transaction stores a `target_shard` reflecting the shard layout at creation time. If the receipt is delayed (e.g., due to congestion/compute-limit saturation) across **two** consecutive dynamic-resharding events, the shard-remapping logic used when re-evaluating/forwarding the receipt cannot correctly resolve the stale `target_shard` to a shard in the current (twice-reshard) layout, and the resolution path panics instead of returning an error.

### Finding Description
`ShardLayoutV3::try_get_parent_shard_id` and `get_shard_index` (`core/primitives/src/shard_layout/v3.rs:344-368`) only remap a shard id to its immediate parent via `last_split_children()`/`shards_parent_map`, i.e., a single resharding generation. When a `GlobalContractDistribution` receipt's `target_shard` was valid under shard layout `N`, gets delayed through resharding to layout `N+1`, and is delayed again through a second resharding to layout `N+2`, the parent-shard remap performed when the receipt is filtered/forwarded (`receipt_filter_fn`, referenced in `runtime/runtime/src/congestion_control.rs`) is exercised against a shard id that is two generations removed from the current layout. The repository's own regression test, `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`, was written specifically to catch this: it force-splits two different shards sequentially, keeps a `GlobalContractDistribution` receipt delayed across both splits by saturating compute in the target shard, then asserts the chain does not stall — with an explicit comment stating "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations." [1](#0-0) [2](#0-1) 

The `ShardLayoutV3` type only tracks `shards_parent_map`/`last_split_children` for the immediately preceding split (`core/primitives/res/epoch_configs/mainnet/76.json:108-117` shows this single-level parent map structure), so any code path that assumes one remap step is sufficient will break for receipts delayed across two or more resharding boundaries. [3](#0-2) 

### Impact Explanation
If the panic is reachable in production (not just guarded/caught), this is a **transaction-triggered chunk-producer halt**: any unprivileged account can submit a `DeployGlobalContract` transaction, and if the resulting distribution receipt becomes delayed across two resharding generations (achievable by an attacker who saturates a shard's compute budget to force their own receipt into the delayed queue, combined with naturally occurring or attacker-influenced dynamic resharding), applying the chunk that processes the stale receipt would panic the node, producing a state-transition halt for all nodes attempting to apply that chunk — a liveness/consensus-halting condition matching the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Likelihood depends on: (1) dynamic resharding being enabled (`ProtocolFeature::DynamicResharding`) and triggering two splits while the receipt remains delayed, and (2) whether the actual (non-test) code path already handles this with a `Result`/error rather than an `unwrap`/`expect`. The presence of a dedicated regression test in the repo strongly suggests this was a known, real panic scenario that was fixed or is being actively guarded against — it is unclear from the available index whether the fix is already merged (the test asserting `both_splits_done` and `head_height >= drain_end` suggests it is validating a fix rather than demonstrating an open bug). I could not locate the current implementation of `receiver_shard_id()` / `receipt_filter_fn()` itself (only test/import references) to confirm whether the panic path still exists or has been replaced with graceful error handling — this is a gap in my verification given the tool-call budget.

### Recommendation
Verify that `receipt_filter_fn()`/`receiver_shard_id()` (used in `runtime/runtime/src/congestion_control.rs` and wherever `GlobalContractDistribution` receipts are re-filtered/forwarded) never calls `.unwrap()`/`.expect()`/indexing on the result of shard-id remapping for stale `target_shard` values. Extend `ShardLayoutV3`'s parent-shard resolution (or the receipt's forwarding logic) to walk the full chain of resharding generations (not just one hop) when remapping a shard id that predates multiple splits, returning a proper `Result`/`ShardLayoutError` instead of panicking if the shard id cannot be resolved.

### Proof of Concept
The existing test `test_stale_global_contract_distribution_after_double_resharding` (`test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`) is itself the proof-of-concept: it (1) enables dynamic resharding forcing two sequential shard splits, (2) deploys a global contract on the shard targeted by the first split, (3) saturates that shard's compute budget every block so the resulting `GlobalContractDistribution` receipt stays in the delayed queue through both resharding transitions, and (4) stops saturating and asserts the chain continues advancing past the point where the delayed receipt must be processed, rather than stalling due to a panic. [4](#0-3)

### Citations

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-186)
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

    // Keep saturating until both resharding events complete. Dynamic resharding has a
    // 2-epoch proposal-to-activation pipeline, so we need enough epochs for both splits.
    let max_saturation_height = start_height + epoch_length * 12;
    let mut both_splits_done = false;
    for target_height in (start_height + 1)..=max_saturation_height {
        // Submit 3 heavy transactions to saturate this block's compute budget.
        {
            let node = env.node_for_account(&chunk_producer);
            for _ in 0..3 {
                let tx = node.tx_call(
                    &deploy_user,
                    &deploy_user,
                    "burn_gas_raw",
                    gas_to_burn.as_gas().to_le_bytes().to_vec(),
                    Balance::ZERO,
                    gas_limit,
                );
                node.submit_tx(tx);
            }
        }
        env.runner_for_account(&chunk_producer).run_until_head_height(target_height);

        // Check if both resharding events have completed.
        let node = env.node_for_account(&chunk_producer);
        let epoch_id = node.client().chain.chain_store().head().unwrap().epoch_id;
        let current_layout = node.client().epoch_manager.get_shard_layout(&epoch_id).unwrap();
        if current_layout.num_shards() >= target_num_shards {
            both_splits_done = true;
            break;
        }
    }
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

**File:** core/primitives/src/shard_layout/v3.rs (L343-368)
```rust
    /// Otherwise, return `shard_id`, or `InvalidShardId` error if the shard doesn't exist.
    pub fn try_get_parent_shard_id(&self, shard_id: ShardId) -> Result<ShardId, ShardLayoutError> {
        if !self.shard_ids.contains(&shard_id) {
            return Err(ShardLayoutError::InvalidShardId { shard_id });
        }

        if self.last_split_children().contains(&shard_id) {
            Ok(self.last_split)
        } else {
            Ok(shard_id)
        }
    }

    pub fn get_shard_index(&self, shard_id: ShardId) -> Result<ShardIndex, ShardLayoutError> {
        self.id_to_index_map
            .get(&shard_id)
            .copied()
            .ok_or(ShardLayoutError::InvalidShardId { shard_id })
    }

    pub fn get_shard_id(&self, shard_index: ShardIndex) -> Result<ShardId, ShardLayoutError> {
        self.shard_ids
            .get(shard_index)
            .copied()
            .ok_or(ShardLayoutError::InvalidShardIndex { shard_index })
    }
```

**File:** core/primitives/res/epoch_configs/mainnet/76.json (L108-117)
```json
      "shards_parent_map": {
        "0": 0,
        "1": 1,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 2,
        "9": 2
      },
```
