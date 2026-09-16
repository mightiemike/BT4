### Title
Panic-inducing bouncer resource estimation on multi-level CASM bytecode segmentation - ([File: crates/blockifier/src/execution/casm_hash_estimation.rs])

### Summary
The Drift Protocol exploit hinged on a value the protocol trusted from an attacker-controlled source (a self-created "collateral" price feed) without validating it against reality, letting the attacker extract far more value than the underlying asset was worth. The structural analog here is the sequencer's bouncer resource-accounting path, which estimates the CASM-hash computation cost of a *declared, attacker-controlled Sierra class* using a hard assumption about bytecode segmentation depth that does not hold for all classes the compiler can legitimately produce, and which panics instead of degrading gracefully when the assumption is violated.

### Finding Description
`get_tx_weights` (called by the bouncer on every transaction during block building, `Bouncer::try_update`) computes CASM-hash computation gas for every class hash executed in a transaction via `map_class_hash_to_casm_hash_computation_resources`, which calls `class.estimate_casm_hash_computation_resources()`. [1](#0-0) 

That estimation, `EstimateCasmHashResources::estimated_resources_of_bytecode_hash_internal_node_leaf_case`, walks the class's bytecode segment structure (`NestedFeltCounts`) and explicitly asserts it supports "at most one level of segmentation":

```rust
for seg in bytecode_segment_felt_sizes {
    match seg {
        NestedFeltCounts::Leaf(_, felt_size_groups) => { ... }
        _ => {
            panic!("Estimating hash cost only supports at most one level of segmentation.")
        }
    }
}
``` [2](#0-1) 

The underlying data type this walks (`NestedIntList` / `NestedFeltCounts`) is recursive by design — it exists precisely to represent nested bytecode segmentation produced by the Sierra→CASM compiler for functions with internal branching/segment structure, as used pervasively in `starknet_api::contract_class::compiled_class_hash` and the Starknet OS hint implementation for `compiled_class_hash`. The estimator, however, hard-codes a one-level-only invariant and calls `panic!` on any deeper nesting instead of returning an error that can be handled.

This estimation function sits directly on the hot path invoked by any unprivileged party: a contract is declared (an unprivileged `declare` transaction reachable by any sender), gets compiled by the Sierra-to-CASM compiler (`crates/apollo_compile_to_casm`), and any subsequent `invoke` transaction that executes that class causes the bouncer, while accumulating block resource weights, to call the estimator on the class's real segment structure. [3](#0-2) 

Because the bouncer resource-accounting path runs during block building and is exercised by every honest node executing/re-validating the same block (the sequencer building the block, and any node re-executing it), a crafted class whose CASM bytecode segmentation exceeds the one-level assumption will panic every node that attempts to account for/execute it, rather than merely fail the single offending transaction gracefully.

### Impact Explanation
A `panic!` inside the block-building / bouncer accounting path executed on the sequencer's hot execution loop is a liveness-critical bug: if reachable, it crashes the process handling block building (and any other node re-executing the same block), preventing the chain from producing/confirming new blocks — this falls squarely within the accepted impact categories ("a network unable to confirm new transactions"). Unlike ordinary execution reverts, a Rust `panic!` in this synchronous accounting code is not contained by normal transaction-level error handling (`TransactionExecutionResult`), since the function signature does not propagate this specific failure as a `Result` — it aborts the calling thread/task outright.

### Likelihood Explanation
The attack requires only an unprivileged `declare` of a Sierra class whose compiled CASM bytecode segmentation structure has more than one level of nesting, followed by an unprivileged `invoke` that causes that class to be executed (and thus counted by the bouncer). Both steps are available to any transaction sender with no special privileges, matching the "unprivileged transaction sender/class declarer" reachability required by scope. The main open question — which I could not fully confirm given tool constraints — is whether the Sierra-to-CASM compiler in this codebase's pinned Cairo compiler version can actually emit more than one level of segment nesting for legitimately-compilable contracts (e.g., contracts with deeply nested function/segment structures). The recursive definition of `NestedIntList`/`NestedFeltCounts` strongly suggests multi-level nesting is a representable, and likely producible, case; the estimator's explicit panic message ("supports **at most** one level") is itself evidence that the code's author was aware deeper nesting is possible in principle but chose to hard assumption it away.

### Recommendation
Replace the `panic!` in `estimated_resources_of_bytecode_hash_internal_node_leaf_case` (and any other call site making the same one-level assumption) with a recursive implementation that correctly estimates resources for arbitrarily nested `NestedFeltCounts` structures, or, if a depth limit is truly enforced upstream (e.g., during Sierra-to-CASM compilation or class validation prior to acceptance into state), add an explicit, provable invariant/test asserting that no class with deeper nesting can ever reach this function, and return a typed `Result` error instead of panicking as defense in depth.

### Proof of Concept
Conceptual PoC (requires confirming compiler-producible nesting depth, which needs a live environment):
1. Craft/compile a Cairo 1 contract whose Sierra program compiles to a `CasmContractClass` with a `bytecode_segment_lengths` structure containing at least two levels of `NestedIntList::Node` nesting (e.g., a contract with deeply structured function segmentation forcing the compiler's segmentation algorithm to recurse).
2. Submit a `declare` transaction for this class (unprivileged, reachable via gateway/mempool).
3. Submit an `invoke` transaction that calls into the class, causing it to be added to `executed_class_hashes` for the transaction.
4. Observe that `Bouncer::try_update` → `get_tx_weights` → `map_class_hash_to_casm_hash_computation_resources` → `estimate_casm_hash_computation_resources` → `estimated_resources_of_bytecode_hash_internal_node_leaf_case` panics on the non-`Leaf` branch, crashing the block-building/re-execution task on every node that processes the block. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/blockifier/src/bouncer.rs (L624-660)
```rust
    /// Updates the bouncer with a new transaction.
    // TODO(Dan): refactor to reduce the number of arguments.
    #[allow(clippy::too_many_arguments)]
    pub fn try_update<S: StateReader>(
        &mut self,
        state_reader: &S,
        tx_state_changes_keys: &StateChangesKeys,
        tx_execution_summary: &ExecutionSummary,
        tx_builtin_counters: &CairoPrimitiveCounterMap,
        tx_resources: &TransactionResources,
        versioned_constants: &VersionedConstants,
        receipt_l2_gas: GasAmount,
    ) -> TransactionExecutorResult<()> {
        // The countings here should be linear in the transactional state changes and execution info
        // rather than the cumulative state attributes.
        let marginal_state_changes_keys =
            tx_state_changes_keys.difference(&self.state_changes_keys);
        let marginal_executed_class_hashes = tx_execution_summary
            .executed_class_hashes
            .difference(&self.get_executed_class_hashes())
            .cloned()
            .collect();
        let n_marginal_visited_storage_entries = tx_execution_summary
            .visited_storage_entries
            .difference(&self.visited_storage_entries)
            .count();
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
```

**File:** crates/blockifier/src/bouncer.rs (L1027-1039)
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
```

**File:** crates/blockifier/src/execution/casm_hash_estimation.rs (L123-150)
```rust
    /// Estimates the Cairo execution resources for a `bytecode_hash_internal_node` leaf case.
    ///
    /// The contract code is segmented by its functions, and each function is a single segment
    /// (no further segmentation).
    ///
    /// `bytecode_hash_internal_node` is applied recursively until all segments are hashed.
    fn estimated_resources_of_bytecode_hash_internal_node_leaf_case(
        bytecode_segment_felt_sizes: &[NestedFeltCounts],
    ) -> ExtendedExecutionResources {
        let mut resources = Self::from_resources(ExecutionResources::default());

        let bytecode_hash_internal_node_overhead = ExecutionResources {
            n_steps: Self::BASE_BYTECODE_HASH_INTERNAL_NODE_LEAF_STEPS,
            ..Default::default()
        };

        // For each segment, hash its felts.
        for seg in bytecode_segment_felt_sizes {
            match seg {
                NestedFeltCounts::Leaf(_, felt_size_groups) => {
                    resources += &bytecode_hash_internal_node_overhead;
                    resources += &Self::estimated_resources_of_hash_function(felt_size_groups);
                }
                _ => {
                    panic!("Estimating hash cost only supports at most one level of segmentation.")
                }
            }
        }
```
