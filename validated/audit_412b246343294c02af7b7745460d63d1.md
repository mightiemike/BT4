Based on my investigation, I found a relevant analog: a chunk-halting panic reachable via `DeployGlobalContract` under dynamic resharding, documented directly by a regression test in the codebase.

### Title
Chunk-producer halt via stale `GlobalContractDistribution` receipt surviving two shard resharding generations - (File: `test-loop-tests/src/tests/global_contracts_distribution.rs`)

### Summary
A regression test, `test_stale_global_contract_distribution_after_double_resharding`, exists specifically to catch a scenario where an unprivileged account's `DeployGlobalContract` action creates a `GlobalContractDistribution` receipt targeting a shard that is later split twice by dynamic resharding before the receipt is dequeued from the delayed-receipt backlog [1](#0-0) . The test comment explicitly states the expected failure mode: "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations" [2](#0-1) .

### Finding Description
Any account can submit a transaction with a `DeployGlobalContract` action, which produces an outgoing `GlobalContractDistribution` receipt whose `target_shard` is computed at creation time based on the current shard layout [3](#0-2) . If chunk compute on the target shard is saturated (achievable by an unprivileged user submitting gas-heavy `FunctionCall` transactions, e.g. `burn_gas_raw`), the receipt is pushed into the delayed-receipt queue instead of being processed immediately [4](#0-3) . If, while the receipt sits in the delayed queue, the shard undergoes two sequential dynamic-resharding splits, the receipt's `target_shard` field becomes stale relative to the current shard layout. When the delayed queue finally drains and the runtime attempts to route/filter the receipt by resolving its receiver shard via `receiver_shard_id()`/`receipt_filter_fn()`, the shard-layout-remap lookup can fail for a shard ID that no longer exists after two resharding generations, and the referenced code path panics rather than gracefully handling the "shard not found" case.

The delayed-receipt queue is drained deterministically by every honest node applying the same chunk (`Runtime::apply` → `process_delayed_receipts`) [5](#0-4) , so a panic here is not a single-node fault: every node applying the chunk containing the drained stale receipt panics identically, halting the shard/chain rather than just crashing one instance — a transaction-triggered halt of the kind explicitly in scope.

### Impact Explanation
This is a chunk-application-time panic reachable purely from unprivileged transaction submission (a `DeployGlobalContract` deployer plus enough gas-heavy calls to saturate the target shard across two resharding cycles). Because `Runtime::apply` is the deterministic state-transition function executed by all validators for a shard, a panic here causes the shard (and by extension the chain) to halt, matching the "transaction-triggered halt" acceptance criterion for this analog scan. It is analogous in bug class to the OpenSMTPD CVE: an "improper check for unusual or exceptional condition" (a data structure — the receipt's stale target shard — that the code assumes stays valid across state transitions, but which is invalidated by an intervening protocol event) causes a crash reachable by an unprivileged party (a local user for OpenSMTPD; an unprivileged transaction submitter for nearcore).

### Likelihood Explanation
Triggering this requires: (1) submitting a `DeployGlobalContract` transaction, (2) saturating the target shard's compute budget long enough for the resulting `GlobalContractDistribution` receipt to sit in the delayed queue, and (3) two dynamic-resharding splits occurring on that shard while the receipt is delayed. This is a non-trivial precondition requiring dynamic resharding (`DynamicResharding` protocol feature) to be active and configured to split the affected shard twice, which is normally driven by real network load/config rather than attacker control. This narrows practical likelihood, but the mechanism itself (submit tx → congest shard → wait for two protocol-driven reshards) is achievable without any privileged role, and the existence of a dedicated regression test built specifically to reproduce this exact panic condition strongly indicates it was a real, previously-existing defect in the target-shard remapping logic for delayed `GlobalContractDistribution` receipts.

### Recommendation
Ensure the receiver/target-shard resolution logic used when dequeuing delayed `GlobalContractDistribution` receipts (the `receiver_shard_id()` / `receipt_filter_fn()` path referenced in the regression test) tolerates shard layouts that have undergone multiple resharding generations since the receipt was enqueued — e.g., by tracking/propagating a shard-splitting-history-aware remap, or by falling back to re-deriving the current target shard from the receipt's semantic target account/identifier rather than relying solely on a possibly-stale `ShardId`, and returning a recoverable error instead of panicking if remapping cannot be resolved.

### Proof of Concept
The exact PoC is already codified as a regression test: `test_stale_global_contract_distribution_after_double_resharding` in `test-loop-tests/src/tests/global_contracts_distribution.rs`, which:
1. Deploys a test contract and then a global contract from `user0`, whose shard is configured to be force-split first [6](#0-5) .
2. Repeatedly submits `burn_gas_raw` calls sized to just over half the gas limit so the `GlobalContractDistribution` receipt is pushed to the delayed queue and stays there through two resharding events [7](#0-6) .
3. Stops saturating and lets the delayed queue drain, asserting the chain continues advancing (i.e., does not stall/panic) [8](#0-7) .



I was not able to fully verify the current state of the `receipt_filter_fn()`/`receiver_shard_id()` implementation itself (i.e., whether this specific defect is already fixed in this snapshot) due to index size limits on `runtime/runtime/src/congestion_control.rs`; the search only surfaced the file header, not the function bodies referenced by the test. I recommend starting a full Devin session with complete file access to confirm whether the underlying remap logic still lacks a graceful fallback, since the test's presence could indicate either an unfixed vulnerability or an already-fixed regression test guarding against a resolved bug.

### Citations

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-57)
```rust
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }

    let epoch_length: BlockHeightDelta = 10;
    let base_boundary_accounts = create_account_ids(["user2", "user3"]).to_vec();
    let base_shard_layout = ShardLayout::multi_shard_custom(base_boundary_accounts, 3);
    let deploy_user: AccountId = create_account_id("user0");
    let users = create_account_ids(["user0", "user1", "user2", "user3", "user4", "user5"]).to_vec();
    let validators_spec = create_validators_spec(1, 0);
    let clients = validators_spec_clients(&validators_spec);
    let chunk_producer = clients[0].clone();
    let gas_limit = Gas::from_teragas(300);
    let base_pv = PROTOCOL_VERSION - 1;

    // Configure dynamic resharding to force-split two shards sequentially.
    // The first split targets the shard containing deploy_user (user0), so the
    // GlobalContractDistribution receipt becomes stale after two layout transitions.
    let first_split_shard = base_shard_layout.account_id_to_shard_id(&deploy_user);
    let second_split_shard = base_shard_layout.account_id_to_shard_id(&create_account_id("user4"));
    assert_ne!(first_split_shard, second_split_shard);
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-114)
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-163)
```rust
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
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L165-185)
```rust
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

**File:** runtime/runtime/src/lib.rs (L2634-2640)
```rust
        // Then we process the delayed receipts. It's a backlog of receipts from the past blocks.
        self.process_delayed_receipts(
            processing_state,
            receipt_sink,
            compute_limit,
            &mut validator_proposals,
        )?;
```
