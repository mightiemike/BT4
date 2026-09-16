### Title
Panic in concurrent worker commit path on non-`BlockFull` bouncer errors causes sequencer DOS - (File: crates/blockifier/src/concurrency/worker_logic.rs)

### Summary
CVE-2018-3284 describes a difficult-to-exploit InnoDB bug where a network-reachable operation triggers a hang/crash (complete DOS) of the database server. The reachable analog in this sequencer is in the concurrent transaction-execution commit path: `WorkerExecutor::commit_tx` unconditionally `panic!`s whenever the bouncer's `try_update` call returns any error other than `TransactionExecutorError::BlockFull`. Because `try_update`'s internal `get_tx_weights` call can propagate a `TransactionExecutionError` (via `?`) for reasons unrelated to block capacity (e.g. state-read/CASM-hash-computation errors), a single crafted transaction that reaches this code path with such a condition crashes the executing worker thread while the block is being built.

### Finding Description
`Bouncer::try_update` computes marginal resource weights for the transaction being committed via `get_tx_weights`, which itself calls `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` against the (versioned) state reader: [1](#0-0) 

Both of those helpers return `TransactionExecutionResult<...>`, and any `Err` is propagated up through `try_update` via `?`, which converts into `TransactionExecutorError::TransactionExecutionError(...)` (not `BlockFull`): [2](#0-1) 

In the concurrent commit path used during block building, `WorkerExecutor::commit_tx` calls `try_update` and explicitly special-cases only `BlockFull`; any other error variant is treated as unrecoverable and the worker thread is made to `panic!`: [3](#0-2) 

The code comment itself flags this as a known gap ("TODO(Avi, 01/07/2024): Consider propagating the error"), acknowledging that any non-`BlockFull` bouncer failure is currently fatal rather than handled gracefully. This is analogous to the CVE's "difficult to exploit" InnoDB condition that leads to a hang/crash rather than a clean, expected error path.

### Impact Explanation
A panic inside a `WorkerExecutor` execution/commit thread halts block production for that worker pool instance; `ConcurrentTransactionExecutor::get_new_results` calls `self.worker_pool.check_panic()`, which is designed to surface worker-thread panics to the caller, effectively aborting the in-progress block build. If a transaction that is otherwise admitted by the mempool/gateway (i.e., passed stateless/stateful validation) can trigger this state-read/weights-computation error only during the sequencer's own concurrent commit phase (a different code path/state view than the gateway's validation), a single such transaction repeatedly resubmitted (or naturally reoccurring) can repeatedly crash/halt block building — a "frequently repeatable crash (complete DOS)" of the sequencer's block-building component, consistent with the CVSS Availability-only impact of the reference CVE.

### Likelihood Explanation
This requires a specific, hard-to-construct state/transaction combination that passes gateway validation but causes `get_tx_weights`'s internal state-dependent computations (CASM hash resource mapping / migration data lookup) to fail during concurrent commit — mirroring the CVE's own "difficult to exploit" / high-complexity rating. I was not able to fully enumerate every internal condition under which `map_class_hash_to_casm_hash_computation_resources` or `CasmHashMigrationData::from_state` return `Err` in this indexed codebase snapshot, so the precise triggering transaction shape could not be fully confirmed from available context.

### Recommendation
In `WorkerExecutor::commit_tx` (`crates/blockifier/src/concurrency/worker_logic.rs`), replace the `panic!` on non-`BlockFull` `TransactionExecutorError`s with graceful error propagation (e.g., surfacing the error through `CommitResult` or halting only the affected transaction/block instead of panicking the worker thread), matching the existing `TODO(Avi, 01/07/2024)` note.

### Proof of Concept
Not fully constructible from the available indexed code: the exact state condition under which `get_tx_weights`'s internal helpers (`map_class_hash_to_casm_hash_computation_resources`, `CasmHashMigrationData::from_state`) return an `Err` reachable from concurrent commit could not be confirmed with certainty in this pass. A concrete PoC would need to identify a declare/invoke sequence where CASM-hash computation or casm-hash migration state lookups fail specifically during the second (validation/commit-phase) state read in `commit_tx`, distinct from the checks already performed during gateway admission — starting a Devin session with full repository access would allow tracing `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` implementations to confirm/refute an unprivileged trigger.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L627-660)
```rust
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
