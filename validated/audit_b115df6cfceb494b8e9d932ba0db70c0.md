Based on my research, the relevant analog in this Starknet sequencer is the use of an **estimated** (not actual) resource cost for CASM-hash computation when accounting for a transaction's bouncer weight, mirroring the GLP report's core flaw: substituting a bounded-but-imprecise estimate for a ground-truth value used in capacity/fee accounting.

### Title
Estimated (not actual) CASM-hash computation resources used for bouncer weight accounting can diverge from true OS/prover cost - (File: `crates/blockifier/src/bouncer.rs`, `crates/blockifier/src/execution/casm_hash_estimation.rs`)

### Summary
When the bouncer computes the weight a transaction contributes to a block (`sierra_gas`/`proving_gas`), it does not measure the actual Cairo execution cost of hashing a newly-executed class's CASM. Instead it calls `estimate_casm_hash_computation_resources`, an empirical, closed-form approximation, and folds that estimate directly into the accepted block capacity accounting [1](#0-0) . This is architecturally identical to `GlpStrategy.pendingRewards()` using a fixed 0.5% slippage estimate instead of the real conversion result.

### Finding Description
`map_class_hash_to_casm_hash_computation_resources` populates the per-class-hash gas contribution to `BouncerWeights.sierra_gas`/`proving_gas` using `class.estimate_casm_hash_computation_resources()` [1](#0-0) , which in turn calls into `CasmV1HashResourceEstimate`/`CasmV2HashResourceEstimate`, explicitly documented as providing "resource estimates rather than exact values" [2](#0-1) .

These estimated weights are what `Bouncer::try_update` uses to decide whether a transaction fits in the remaining block capacity, and they are the values propagated into the final `BouncerWeights`/`CasmHashComputationData` committed as part of `BlockExecutionSummary` [3](#0-2) [4](#0-3) .

The repository's own tests prove that "estimate" and "actual" are not the same quantity: `compare_estimated_vs_actual_casm_hash_resources` explicitly diffs `estimated_resources` against `actual_execution_resources` obtained by running the real `compiled_class_hash` entry point, and only asserts the divergence stays under a hard-coded `ALLOWED_MARGIN_N_STEPS` / `allowed_margin_blake_opcode_count` tolerance [5](#0-4) [6](#0-5) . This margin is only validated against a fixed suite of feature contracts, not proven as a hard invariant over arbitrary Sierra/CASM bytecode a declarer could submit — exactly the "estimate with an accepted slippage tolerance that isn't guaranteed to hold under adversarial/market conditions" pattern from the GLP report, except here the "market condition" is the *bytecode shape* an unprivileged declarer chooses.

### Impact Explanation
Because the estimate (not the OS's actual measured cost) is what the bouncer uses to admit transactions into a block and to determine block fullness, an attacker who declares a class whose bytecode/entry-point layout is crafted to maximize the actual-vs-estimated gap (beyond what the fixed test suite's `ALLOWED_MARGIN_N_STEPS` covers) can cause the accepted block's true CASM-hash computation cost (as later measured by Starknet OS re-execution/proving) to exceed the bouncer-enforced `block_max_capacity` that was used at block-building time. This creates a divergence between what the sequencer/bouncer believed it packed and what the OS/prover must actually execute, risking either block-proving failure (a network unable to confirm/finalize the block) or a resource/weight commitment (`casm_hash_computation_data_sierra_gas`/`proving_gas`, which is part of the committed block execution summary) that does not match reality.

### Likelihood Explanation
Medium: any unprivileged account can submit a DECLARE transaction with an arbitrary class whose bytecode segmentation and entry-point structure are attacker-chosen, i.e., this is reachable from a single submitted transaction/declared class as required. The likelihood of exceeding the specific tolerated margin depends on how tightly the estimate formula (`CasmV1HashResourceEstimate`/`CasmV2HashResourceEstimate`) tracks real Cairo VM step counts across the full space of legal bytecode shapes, which is only empirically validated on a bounded feature-contract set, not proven as a bound for all inputs.

### Recommendation
Either (a) reconcile bouncer weight/committed `CasmHashComputationData` against the *actual* Starknet OS execution resources for CASM-hash computation once the class is truly hashed (rather than relying purely on a pre-execution estimate), or (b) formally bound the estimator's worst-case error over the full domain of valid CASM bytecode segment structures/entry-point layouts (not just the tested feature-contract corpus), and size the bouncer's safety margin to that proven worst case rather than an empirically-observed one.

### Proof of Concept
Not directly demonstrable without executing the full block-building/OS pipeline, but the reproduction path is:
1. Craft a Cairo1 contract whose bytecode segmentation (`NestedFeltCounts`) and entry-point layout maximize the gap between `CasmV2HashResourceEstimate::estimated_resources_of_compiled_class_hash` and the actual Blake/Poseidon hashing cost measured by `run_compiled_class_hash_entry_point` (see the divergence-measuring harness at [5](#0-4) ).
2. Submit it via a DECLARE transaction so `map_class_hash_to_casm_hash_computation_resources` records the (too-low) estimate into the block's bouncer weights [1](#0-0) .
3. Pack the block up to the bouncer's `sierra_gas`/`proving_gas` capacity using the underestimated weight, then let the OS re-execute/prove the block and observe the actual resource consumption exceeds the capacity the bouncer believed it enforced.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L650-695)
```rust
        let tx_weights = get_tx_weights(
            state_reader,
            &marginal_executed_class_hashes,
            n_marginal_visited_storage_entries,
            tx_resources,
            &marginal_state_changes_keys,
            versioned_constants,
            tx_builtin_counters,
            &self.bouncer_config,
            receipt_l2_gas,
        )?;

        let tx_bouncer_weights = tx_weights.bouncer_weights;

        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
        }

        self.update(tx_weights, tx_execution_summary, &marginal_state_changes_keys);

        Ok(())
    }
```

**File:** crates/blockifier/src/bouncer.rs (L1027-1040)
```rust
/// Returns a mapping from each class hash to its estimated Cairo resources for Casm hash
/// computation (done by the OS).
pub fn map_class_hash_to_casm_hash_computation_resources<S: StateReader>(
    state_reader: &S,
    executed_class_hashes: &HashSet<ClassHash>,
) -> TransactionExecutionResult<HashMap<ClassHash, ExtendedExecutionResources>> {
    executed_class_hashes
        .iter()
        .map(|class_hash| {
            let class = state_reader.get_compiled_class(*class_hash)?;
            Ok((*class_hash, class.estimate_casm_hash_computation_resources()))
        })
        .collect()
}
```

**File:** crates/blockifier/src/execution/casm_hash_estimation.rs (L19-25)
```rust
/// Trait for estimating the Cairo execution resources consumed when running the
/// `compiled_class_hash` function in the Starknet OS.
///
/// Varied implementations of this trait correspond to a specific hash function used by
/// `compiled_class_hash`.
///
/// This provides resource estimates rather than exact values.
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L281-306)
```rust
    // Take CasmHashComputationData from bouncer,
    // and verify that class hashes are the same.
    let casm_hash_computation_data_sierra_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_sierra_gas());
    let casm_hash_computation_data_proving_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_proving_gas());

    assert_eq!(
        casm_hash_computation_data_sierra_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        casm_hash_computation_data_proving_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>()
    );

    Ok(BlockExecutionSummary {
        state_diff: state_diff.into(),
        compressed_state_diff,
        bouncer_weights: *bouncer.get_bouncer_weights(),
        casm_hash_computation_data_sierra_gas,
        casm_hash_computation_data_proving_gas,
        compiled_class_hashes_for_migration: class_hashes_to_migrate.into_values().collect(),
        block_info: block_context.block_info.clone(),
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/compiled_class_test.rs (L53-65)
```rust
// V1 (Poseidon) HASH CONSTS
/// Expected Poseidon hash for the Cairo1 ERC20 feature contract (committed `erc20.casm.json`).
const EXPECTED_V1_HASH: expect_test::Expect =
    expect!["1086536622945160114536053561878005579687531094896980931390443771221568164185"];
const EXPECTED_BUILTIN_USAGE_FULL_CONTRACT_V1_HASH: expect_test::Expect =
    expect!["poseidon_builtin: 11928"];
const EXPECTED_N_STEPS_FULL_CONTRACT_V1_HASH: Expect = expect!["136909"];
// Expected execution resources for loading partial contract.
const EXPECTED_BUILTIN_USAGE_PARTIAL_CONTRACT_V1_HASH: expect_test::Expect =
    expect!["poseidon_builtin: 221, range_check_builtin: 85"];
const EXPECTED_N_STEPS_PARTIAL_CONTRACT_V1_HASH: Expect = expect!["6382"];
// Allowed margin between estimated and actual execution resources.
const ALLOWED_MARGIN_N_STEPS: usize = 127;
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/compiled_class_test.rs (L555-612)
```rust
fn compare_estimated_vs_actual_casm_hash_resources(
    contract_name: &str,
    contract_class: CasmContractClass,
    hash_version: &HashVersion,
) {
    // Run the compiled class hash entry point with full contract loading.
    let load_full_contract = true;
    let accessed_segments_indicator = AccessSegmentsIndicator::none();
    let (actual_execution_resources, actual_opcode_instances, _) =
        run_compiled_class_hash_entry_point(
            &contract_class,
            load_full_contract,
            &accessed_segments_indicator,
            hash_version,
        )
        .unwrap();

    let bytecode_segments = NestedFeltCounts::new(
        &contract_class.get_bytecode_segment_lengths(),
        &contract_class.bytecode,
    );

    // Estimate resources.
    let estimated_resources = hash_version.estimate_execution_resources(
        &bytecode_segments,
        &contract_class.entry_points_by_type.into(),
    );

    // Compare n_steps.
    let n_steps_margin =
        estimated_resources.vm_resources.n_steps.abs_diff(actual_execution_resources.n_steps);
    let allowed_n_steps_margin = hash_version.allowed_margin_n_steps();
    assert!(
        n_steps_margin <= allowed_n_steps_margin,
        "{contract_name}: Estimated n_steps differ from actual by more than \
         {allowed_n_steps_margin}. Margin: {n_steps_margin}"
    );

    // Compare builtins.
    assert_eq!(
        estimated_resources.vm_resources.builtin_instance_counter,
        actual_execution_resources.filter_unused_builtins().builtin_instance_counter,
        "{contract_name}: Estimated builtins do not match actual builtins"
    );

    // Compare Blake opcode count.
    let estimated_blake_opcode_count =
        estimated_resources.opcode_instance_counter.get(&OpcodeName::blake).copied().unwrap_or(0);
    let actual_blake_opcode_count =
        actual_opcode_instances.get(&OpcodeName::blake).copied().unwrap_or(0);
    let blake_opcode_count_margin =
        estimated_blake_opcode_count.abs_diff(actual_blake_opcode_count);
    let allowed_blake_opcode_count_margin = hash_version.allowed_margin_blake_opcode_count();
    assert!(
        blake_opcode_count_margin <= allowed_blake_opcode_count_margin,
        "{contract_name}: Estimated Blake opcode count differs from actual by more than \
         {allowed_blake_opcode_count_margin}. Margin: {blake_opcode_count_margin}"
    );
```
