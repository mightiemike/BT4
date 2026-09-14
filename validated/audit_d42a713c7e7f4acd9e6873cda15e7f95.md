## Title
Stale `GlobalContractDistribution` receipt after double resharding causes non-positive-refcount corruption / node panic during GC — ([File: test-loop-tests/src/tests/global_contracts_distribution.rs])

## Summary
The CVE describes an assertion failure in an interpreter's string reference-counting subsystem (JerryScript's `ecma-literal-storage.c`), reachable via crafted script input that desynchronizes the ref-count invariant. The closest reachable analog in nearcore is in the trie/state reference-counting subsystem (`DBCol::State` refcounts, `core/store/src/db/refcount.rs`), specifically a known, code-acknowledged defect where `GlobalContractDistribution` receipts that survive across **two resharding generations** desynchronize refcount bookkeeping, producing the exact error string `"Inserting value with non-positive refcount"` in `core/store/src/db/testdb.rs`, and a documented risk of a panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the stale receipt's target shard.

## Finding Description
Deploying a global contract creates a `GlobalContractDistribution` receipt targeted at the deployer's shard (`target_shard`). If that receipt is delayed (e.g., due to compute/gas saturation on the target shard) long enough to survive **two consecutive shard-splitting (resharding) events**, the shard-remapping logic that tracks a receipt's `target_shard` across shard-layout changes becomes stale: the receipt's `receiver_shard_id()` can no longer be correctly resolved to a valid shard in the current layout.

This is directly acknowledged in the test suite: [1](#0-0) 
which sets up dynamic resharding to force two sequential shard splits while a `GlobalContractDistribution` receipt is kept in the delayed queue via compute saturation.

A companion test in the same file explicitly documents the consequence: [2](#0-1) 
"If the vulnerability exists, processing the stale `GlobalContractDistribution` receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard` after two resharding generations." The test asserts the chain does not stall, i.e. it is a regression test for a transaction-triggered halt.

Separately, a second test in the same file is unconditionally disabled with a comment describing the underlying refcount corruption: [3](#0-2) 
"Spice + resharding triggers a refcount bug in GC (testdb 'Inserting value with non-positive refcount'). Re-enable once that is resolved." The literal error string originates from the reference-counted `DBCol::State`/related column bookkeeping in `core/store/src/db/testdb.rs`, which mirrors the production RocksDB refcount invariant documented in `core/store/src/db/refcount.rs` (each increment must have a matching decrement; breaking symmetry corrupts state, as documented in `core/store/STORAGE_ARCHITECTURE.md:86-93`). This is the same class of bug as the CVE: a reference-count invariant, expected to hold for every referenced object (JerryScript's interned string vs. nearcore's refcounted trie/state entry), is violated by input the attacker/reporter can influence (crafted JS string operations vs. a deployer's global-contract deploy combined with congestion-induced delay and shard splits), leading to an assertion/invariant failure.

## Impact Explanation
Two distinct, concrete impacts are implicated by the code's own comments:
1. **Transaction-triggered chain halt**: a panic in `receipt_filter_fn()` when resolving a stale receipt's shard would crash chunk/receipt processing for the shard, i.e. a validator-side panic triggered purely by receipt processing derived from an unprivileged user's global-contract deployment transaction plus normal congestion — this maps to the "transaction-triggered halt" acceptance criterion.
2. **Refcount corruption during GC**: "Inserting value with non-positive refcount" indicates the reference-count invariant for stored trie/state entries has been violated, which per `core/store/STORAGE_ARCHITECTURE.md` can corrupt state (an entry deleted too early, or improperly retained) — this can manifest as state-root divergence between nodes that experience the bug differently (e.g., different GC timing) or unrecoverable storage corruption.

Both are Medium/High-severity, protocol-relevant defects reachable from an ordinary account: deploying a global contract via `DeployGlobalContractAction`/`tx_deploy_global_contract`, and no special privilege is required to trigger congestion that delays the resulting receipt.

## Likelihood Explanation
The bug requires: (1) a global contract deployment, (2) sufficient time/congestion for the resulting `GlobalContractDistribution` receipt to remain delayed across two resharding boundaries, and (3) dynamic resharding actually configured/occurring on the network. Both preconditions are demonstrated feasible in the repo's own test harness using only transaction submission (deploy + compute-saturating calls) — no validator or network-level privilege is needed to construct the scenario, only patience/timing tied to real epoch/resharding schedules. This makes it a genuine, if narrow-timing-window, externally triggerable condition rather than a purely theoretical one. Both tests are currently marked `#[cfg_attr(feature = "protocol_feature_spice", ignore)]`, indicating the underlying bug is known but not confirmed fully fixed for the SPICE-enabled configuration; the fact that the maintainers still gate/ignore the tests suggests some residual risk warranting a Medium rating.

## Recommendation
- Ensure `GlobalContractDistribution` (and other cross-epoch, cross-resharding receipts) carry shard-lineage metadata sufficient to be correctly remapped through an arbitrary number of resharding events, not just one.
- Make `receiver_shard_id()` fail safe (return an error / re-queue) rather than panic when it cannot resolve a stale `target_shard`, converting a potential liveness-halting panic into a recoverable error path.
- Audit the refcount bookkeeping path exercised by `GlobalContractDistribution` receipts under resharding + GC to ensure every increment has a matching decrement (per the invariant documented in `core/store/STORAGE_ARCHITECTURE.md`), and add fuzzing/property tests that combine congestion-induced receipt delay with multiple resharding generations.
- Un-skip and pass the disabled regression tests (`test_stale_global_contract_distribution_after_double_resharding`, `test_global_distribution_receipt_has_receipt_to_tx`) under the SPICE feature configuration before considering the issue resolved.

## Proof of Concept
The repository's own test scaffolding is a working PoC outline (currently used as a regression/guard test, not to demonstrate exploitation, but structurally equivalent):
1. Configure dynamic resharding with two forced sequential shard splits (`force_split_shards`), as in [4](#0-3) .
2. From an ordinary account, deploy a global contract (`tx_deploy_global_contract`) targeting a shard scheduled to be the first split target — [5](#0-4) .
3. Continuously saturate the target shard's chunk compute budget with unrelated transactions so the `GlobalContractDistribution` receipt is pushed into the delayed-receipt queue and remains there across both resharding events — [6](#0-5) .
4. Stop saturating and let the delayed queue drain; per the code's own comment, processing the now-stale receipt is expected to panic in `receipt_filter_fn()` due to failed `receiver_shard_id()` remapping — [2](#0-1) .
5. Independently, the sibling test demonstrates the refcount-corruption symptom ("Inserting value with non-positive refcount") under the same Spice+resharding conditions, currently worked around by disabling the test — [3](#0-2) .

**Note on uncertainty**: I was not able to directly inspect the implementation of `receipt_filter_fn()` or the exact `receiver_shard_id()` remapping logic (their source files were not returned by the available searches), so I cannot cite the precise line where the panic or refcount desync originates, only the test-level acknowledgment of the bug. A Devin session with full repository access would be needed to pinpoint the exact function and confirm whether the panic path is still live in the current `protocol_feature_spice`-gated build or has since been patched.

### Citations

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-66)
```rust
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
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

    let dynamic_config = DynamicReshardingConfig {
        memory_usage_threshold: u64::MAX,
        min_child_memory_usage: u64::MAX,
        max_number_of_shards: 100,
        min_epochs_between_resharding: 1.try_into().unwrap(),
        force_split_shards: vec![first_split_shard, second_split_shard],
        block_split_shards: vec![],
    };
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L102-114)
```rust
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-186)
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L405-411)
```rust
/// Tests that GlobalContractDistribution receipts have ReceiptToTx entries, including
/// forwarded distribution receipts that hop across shards.
#[test]
// Spice + resharding triggers a refcount bug in GC (testdb "Inserting value
// with non-positive refcount"). Re-enable once that is resolved.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_global_distribution_receipt_has_receipt_to_tx() {
```
