## Title
`Bouncer::try_update` panics in concurrent commit flow instead of gracefully rejecting a transaction, halting block building - (File: `crates/blockifier/src/concurrency/worker_logic.rs`)

### Summary
The Sherlock report describes `Vault.blacklistProtocol`, an "emergency" function that unconditionally performs a withdrawal that can revert, thereby blocking the very operation meant to remove a broken/blacklisted protocol from the system. The analogous pattern in this codebase is the concurrent transaction commit path, `WorkerExecutor::commit_tx`, which unconditionally treats any bouncer-update failure other than `BlockFull` as an unrecoverable condition and `panic!`s, instead of gracefully rejecting/retrying the offending transaction, aborting the block-building process for the whole worker pool.

### Finding Description
During concurrent (multi-worker) block building, once a transaction's execution is finalized, `commit_tx` calls `Bouncer::try_update` to check whether the transaction fits into the block and to update the accumulated block weights: [1](#0-0) 

If `try_update` returns any error other than `TransactionExecutorError::BlockFull`, the code explicitly panics:
```rust
match error {
    TransactionExecutorError::BlockFull => return Ok(CommitResult::NoRoomInBlock),
    _ => {
        // TODO(Avi, 01/07/2024): Consider propagating the error.
        panic!("Bouncer update failed. {error:?}: {error}");
    }
}
``` [2](#0-1) 

`try_update` itself computes per-transaction weights via `get_tx_weights`, which reads state to compute CASM-hash computation gas and CASM-hash migration data for every class executed by the transaction: [3](#0-2) 

Both `map_class_hash_to_casm_hash_computation_resources` and `CasmHashMigrationData::from_state` perform state reads (`state_reader.get_compiled_class`, `get_compiled_class_hash`, `get_compiled_class_hash_v2`, etc.) that can fail with `StateError` / `ProgramError` / other `TransactionExecutionError` variants that are unrelated to block capacity, and these propagate through `?` in `get_tx_weights` up into `try_update`: [4](#0-3) 

In addition, `try_update` also contains an explicit `expect`-based panic on `checked_add` overflow when accumulating weights: [5](#0-4) 

Just like `Vault.blacklistProtocol`, which unconditionally calls `withdrawFromProtocol` (an operation that can legitimately fail after the protocol is compromised) inside a function whose purpose is to remove/finalize handling of a problematic entity, `commit_tx` unconditionally treats a bouncer-computation failure — which can originate from state/class data associated with a single (potentially adversarial or unusual) transaction — as fatal, converting a per-transaction issue into a whole-process panic rather than rejecting only that transaction.

### Impact Explanation
A panic in `commit_tx` aborts the worker thread executing the block-building chunk. Since this is on the block-production hot path (concurrent execution used by the batcher to build proposals), a transaction that reliably triggers a non-`BlockFull` error inside `get_tx_weights`/`try_update` (e.g., through state/class conditions reachable from a declared class or executed contract) can crash the sequencer's block builder, preventing it from producing new blocks — i.e., a network unable to confirm new transactions until the faulty transaction is manually filtered out. This matches the impact class of concrete freezing / inability to progress that this exercise accepts.

### Likelihood Explanation
Reaching this code path requires only that a transaction be selected for execution and reach the commit stage in concurrency mode; no special privileges are needed — any account that can submit an invoke/declare transaction that ends up producing an error during CASM-hash/migration-gas computation (rather than merely exceeding capacity) can trigger it. The severity in the original report was disputed as "unlikely to cause loss of funds," and similarly here the likelihood depends on finding/crafting a transaction whose executed class state triggers a `get_tx_weights` error outside of `BlockFull`; this requires further investigation into which specific state conditions are reachable by an ordinary user (vs. protected by earlier validation), so likelihood should be treated as Medium pending such confirmation.

### Recommendation
`commit_tx` (and analogously `try_update`) should not panic on non-`BlockFull` bouncer errors. Instead:
- Treat unexpected `try_update` errors defensively: reject/skip the transaction (similar to `BlockFull`) and log the concrete error, or propagate it as a recoverable `TransactionExecutorError` to the caller so the block-building loop can exclude the transaction and continue, rather than crashing the whole executor.
- Replace the `expect`-based overflow panic in `try_update`'s `checked_add` with a saturating/explicit error path that is also handled gracefully by `commit_tx`.

### Proof of Concept
1. Craft/declare a transaction whose executed class hash causes `map_class_hash_to_casm_hash_computation_resources` or `CasmHashMigrationData::from_state` to return an error (e.g., a state condition causing `StateError`/`ProgramError` rather than a capacity overflow) when read during `get_tx_weights`.
2. Submit the transaction so it is scheduled and executed in concurrency mode; upon execution success, `commit_tx` calls `bouncer.try_update(...)`.
3. `try_update` propagates the non-`BlockFull` error from `get_tx_weights`, and `commit_tx`'s `match` hits the `_ => panic!(...)` arm, crashing the worker performing block building. [2](#0-1)

### Citations

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

**File:** crates/blockifier/src/bouncer.rs (L650-660)
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
```

**File:** crates/blockifier/src/bouncer.rs (L664-670)
```rust
        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
```

**File:** crates/blockifier/src/bouncer.rs (L911-948)
```rust
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
