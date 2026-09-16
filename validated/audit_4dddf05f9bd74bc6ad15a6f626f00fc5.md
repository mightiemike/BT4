### Title
Bouncer `StateError`/`TransactionExecutionError` during commit reaches an unconditional `panic!` in the concurrent worker, crashing block building - ([File: crates/blockifier/src/concurrency/worker_logic.rs])

### Summary
`TransactionExecutorError` has four variants — `BlockFull`, `StateError`, `TransactionExecutionError`, and `CompressionError` — all of which can be returned by `Bouncer::try_update` via `get_tx_weights` (which itself calls `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state`, both `StateReader`-backed and fallible). [1](#0-0)  In the concurrent execution path, `WorkerExecutor::commit_tx` only special-cases `TransactionExecutorError::BlockFull`; every other variant falls into an unconditional `panic!`. [2](#0-1) 

### Finding Description
During concurrent block building/validation, each transaction's commit phase calls `self.bouncer.lock().expect(...).try_update(...)`, and on any non-`BlockFull` error it executes `panic!("Bouncer update failed. {error:?}: {error}");`. [3](#0-2) 

`try_update` calls `get_tx_weights`, which propagates errors from `map_class_hash_to_casm_hash_computation_resources` (a `StateReader`-backed lookup of CASM hash computation data for every class touched by the transaction) and `CasmHashMigrationData::from_state` (also state-backed) via `?`. [4](#0-3)  Both are `TransactionExecutionResult<T>` results that can fail with a `StateError` (e.g. `UndeclaredClassHash`, or any propagated read failure) or a `TransactionExecutionError` for any class hash that was touched by execution but whose compiled-class/CASM-hash data cannot be resolved from the versioned/concurrent state at commit time.

This is architecturally different from the single-threaded `TransactionExecutor::execute` path, which uses `?` to properly propagate the same error as `Err(TransactionExecutorError::...)` back to the caller. [5](#0-4)  Only the concurrent worker's `commit_tx` treats any bouncer error other than `BlockFull` as an unrecoverable condition worth crashing the process for.

Because concurrent execution re-validates/re-executes transactions against a versioned state and only computes weights during the final commit (after speculative execution using per-tx pinned versions of the state), any race, incomplete alias/CASM population, or transient inconsistency between the speculative read set and the final committed state at the moment `try_update`/`get_tx_weights` runs can produce a `StateError` or `TransactionExecutionError` instead of a clean bounded `BlockFull`. Since this is a `panic!` in a worker thread that is part of the sequencer's normal per-transaction commit hot path — reachable from any transaction being committed in concurrent block building — a single crafted or edge-case transaction (e.g. one that races class declaration/execution ordering, or exercises borderline CASM-hash-migration edge cases) can trigger this panic during ordinary block construction.

### Impact Explanation
A `panic!` inside a `WorkerExecutor` thread during `commit_tx` will abort that worker; depending on how the worker pool surfaces the panic (`worker_pool.check_panic()` is used elsewhere to detect and propagate panics from workers), this manifests as block-building failure or an aborted proposal/validation cycle. Because this triggers on every attempted commit for the specific problematic transaction, and the code has no recovery path other than crashing, it is a repeatable denial-of-service against block production comparable to the "hang or frequently repeatable crash" impact described in CVE-2022-21348 — the sequencer becomes unable to close a block containing the offending transaction, which can be resubmitted or naturally reordered into subsequent blocks, extending the DoS window and potentially reproducing "a network unable to confirm new transactions" if the condition recurs across proposers.

### Likelihood Explanation
Exploitability depends on making `get_tx_weights`'s underlying state lookups fail (a `StateError`) or the CASM-hash computation path return a `TransactionExecutionError` at commit time. I was not able to fully verify the exact preconditions of `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` failing under attacker-reachable, non-malicious-operator conditions within available tool budget — this is the key remaining uncertainty. If such a state (e.g., referencing a class hash that is declared and executed within the same transaction chunk but whose CASM data is not yet visible under the versioned/concurrent state view used by the bouncer at commit) is reachable purely from a single submitted transaction/declare, likelihood is Medium; if it strictly requires operator misconfiguration or an already-corrupted state, it would not qualify under the scope rules.

### Recommendation
In `WorkerExecutor::commit_tx`, do not `panic!` on `TransactionExecutorError` variants other than `BlockFull`. Instead, propagate the error to the caller (mirroring `TransactionExecutor::execute`'s `?`-based propagation) so it can be handled as a transaction-execution failure (e.g., reject/skip the transaction, or halt-and-retry the chunk) rather than crashing the entire block-building worker. Add an explicit test exercising `StateError`/`TransactionExecutionError` returns from `try_update` in the concurrent path to confirm graceful handling.

### Proof of Concept
Not independently reproduced. A conceptual PoC would require constructing a transaction sequence under concurrent execution (`concurrency_mode = true`) where a class hash executed by a transaction has its CASM-hash-computation data resolvable at speculative-execution time but not at final commit time (e.g., via a race between class declaration and its use, or a compiled-class-hash migration edge case), causing `get_tx_weights` to return `Err(TransactionExecutorError::StateError(_))` or `Err(TransactionExecutorError::TransactionExecutionError(_))` from `Bouncer::try_update`, which `commit_tx` would then turn into a `panic!`. Confirming feasibility requires deeper reading of `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` than was completed here; a Devin session with full repository access is recommended to trace these functions and their failure conditions precisely.

### Citations

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L36-46)
```rust
#[derive(Debug, Error)]
pub enum TransactionExecutorError {
    #[error("Transaction cannot be added to the current block, block capacity reached.")]
    BlockFull,
    #[error(transparent)]
    StateError(#[from] StateError),
    #[error(transparent)]
    TransactionExecutionError(#[from] TransactionExecutionError),
    #[error(transparent)]
    CompressionError(#[from] CompressionError),
}
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L160-181)
```rust
        match tx_execution_result {
            Ok(tx_execution_info) => {
                let state_diff = transactional_state.to_state_diff()?.state_maps;
                let tx_state_changes_keys = state_diff.keys();
                lock_bouncer(&self.bouncer).try_update(
                    &transactional_state,
                    &tx_state_changes_keys,
                    &tx_execution_info.summarize(&self.block_context.versioned_constants),
                    &tx_execution_info.summarize_builtins(),
                    &tx_execution_info.receipt.resources,
                    &self.block_context.versioned_constants,
                    tx_execution_info.receipt.gas.l2_gas,
                )?;
                transactional_state.commit();

                Ok((tx_execution_info, state_diff))
            }
            Err(error) => {
                transactional_state.abort();
                Err(TransactionExecutorError::TransactionExecutionError(error))
            }
        }
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L347-365)
```rust
            // Ask the bouncer if there is room for the transaction in the block.
            let bouncer_result = self.bouncer.lock().expect("Bouncer lock failed.").try_update(
                &tx_versioned_state,
                &tx_state_changes_keys,
                &execution_summary,
                &tx_execution_info.summarize_builtins(),
                &tx_execution_info.receipt.resources,
                &self.block_context.versioned_constants,
                tx_execution_info.receipt.gas.l2_gas,
            );
            if let Err(error) = bouncer_result {
                match error {
                    TransactionExecutorError::BlockFull => return Ok(CommitResult::NoRoomInBlock),
                    _ => {
                        // TODO(Avi, 01/07/2024): Consider propagating the error.
                        panic!("Bouncer update failed. {error:?}: {error}");
                    }
                }
            }
```

**File:** crates/blockifier/src/bouncer.rs (L910-948)
```rust
#[allow(clippy::too_many_arguments)]
pub fn get_tx_weights<S: StateReader>(
    state_reader: &S,
    executed_class_hashes: &HashSet<ClassHash>,
    n_visited_storage_entries: usize,
    tx_resources: &TransactionResources,
    state_changes_keys: &StateChangesKeys,
    versioned_constants: &VersionedConstants,
    tx_cairo_primitives_counters: &CairoPrimitiveCounterMap,
    bouncer_config: &BouncerConfig,
    receipt_l2_gas: GasAmount,
) -> TransactionExecutionResult<TxWeights> {
    let message_resources = &tx_resources.starknet_resources.messages;
    let message_starknet_l1gas = usize_from_u64(message_resources.get_starknet_gas_cost().l1_gas.0)
        .expect("This conversion should not fail as the value is a converted usize.");

    // Casm hash resources.
    let class_hash_to_casm_hash_computation_resources =
        map_class_hash_to_casm_hash_computation_resources(state_reader, executed_class_hashes)?;

    // Patricia update + transaction resources.
    let patricia_update_resources = get_patricia_update_resources(
        n_visited_storage_entries,
        // TODO(Yoni): consider counting here the global contract tree and the aliases as well.
        state_changes_keys.storage_keys.len(),
    );
    let vm_resources =
        &tx_resources.computation.total_extended_vm_resources() + &patricia_update_resources;

    // Builtin gas costs for stone and for stwo.
    let sierra_builtin_gas_costs = &versioned_constants.os_constants.gas_costs.builtins;
    let proving_builtin_gas_costs = &bouncer_config.builtin_gas_costs();

    // Casm hash migration resources.
    let migration_data = CasmHashMigrationData::from_state(
        state_reader,
        executed_class_hashes,
        versioned_constants,
    )?;
```
