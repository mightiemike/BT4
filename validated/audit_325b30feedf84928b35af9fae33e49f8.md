### Title
Block-Wide DoS via Unchecked Aggregation of Per-Transaction CASM Hash Migration Data in `finalize_block` - (File: crates/blockifier/src/blockifier/transaction_executor.rs)

### Summary
`finalize_block()` aggregates `class_hashes_to_migrate` across **every transaction executed in the block** (via the shared `Bouncer`) and then applies them in a single loop with `block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?`, propagating any single failure with `?` and additionally containing a hard `assert_ne!` inside the per-entry loop. This mirrors the Mellow pattern: data contributed by one transaction (an unprivileged sender executing/declaring an old-hash class) is blindly merged into a block-wide aggregate structure and later iterated without per-entry isolation, so one bad/edge-case entry aborts processing for the whole batch rather than just the offending transaction.

### Finding Description
`CasmHashMigrationData::from_state` builds per-transaction migration entries from `should_migrate()` [1](#0-0)  and `should_migrate` itself is reachable purely by having a transaction execute any class hash whose stored compiled-class-hash is V1 [2](#0-1) . These entries are merged into the shared, block-scoped `Bouncer.accumulated_weights.class_hashes_to_migrate` on every `try_update`/`update` call, with no filtering: `self.accumulated_weights.class_hashes_to_migrate.extend(tx_weights.class_hashes_to_migrate);` [3](#0-2) .

At block-close time, `finalize_block` takes this whole aggregated map and feeds it in one shot to `set_compiled_class_hash_migration`: `block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;` [4](#0-3) . That function loops over **all** migration entries contributed by **all** transactions and, for each one, executes a hard invariant check before writing state: `assert_ne!(compiled_class_hash_v1, compiled_class_hash_v2, ...)` followed by `self.set_compiled_class_hash(*class_hash, *compiled_class_hash_v2)?;` [5](#0-4) . The code's own comment flags this as an unresolved design smell: `// TODO(Meshi): Consider panic here instead of returning an error.` — i.e., today it is *already* a mix of `assert_ne!` panic and `?`-propagated `StateError`, both of which abort finalization for the **entire block**, not just the contributing transaction.

This is the direct analog of the Mellow bug: in Mellow, one subvault's unchecked asset triggers a revert during `hookPullAssets()` that aggregates over *all* subvaults, killing redemption for everyone. Here, one transaction's migration entry (built from state that could, under concurrent block-building — see `worker_logic::commit_tx`, which merges `marginal_executed_class_hashes` and calls `bouncer.try_update` per committed tx — become stale relative to other concurrently-committed transactions touching the same class hash) can make `finalize_block()` fail/panic for the block as a whole via `close_block()` [6](#0-5) , propagating up through `TransactionExecutorTrait::close_block` in the batcher [7](#0-6)  and ultimately into `BlockBuilder::build_block_inner` as a `BlockBuilderError` [8](#0-7) .

### Impact Explanation
If a transaction (declaring/executing a class whose migration entry becomes internally inconsistent by the time `finalize_block` runs, e.g. due to concurrent execution reordering across the multiple transactions that share the class hash) causes the `assert_ne!` to fail, the process **panics** while closing a block. Because this code path runs during proposal building (`propose_block`) and validation (`validate_block`) for every sequencer/validator node processing that block, a reproducible trigger would cause the network to be unable to confirm the affected block — a liveness/availability failure reachable from a single, unprivileged transaction submission, matching the "network unable to confirm new transactions" acceptance criterion. If instead `set_compiled_class_hash` returns a `StateError` (the `?` path), the effect is a full block-close failure rather than isolating/rejecting only the offending transaction, again a block-wide DoS caused by one transaction's contribution to a blindly-aggregated map.

### Likelihood Explanation
Likelihood is **uncertain/lower** than the original Mellow report because:
- `should_migrate()` explicitly filters out entries where `state_compiled_class_hash == compiled_class_hash_v2` before insertion, so under normal sequential execution the `assert_ne!` should not fire.
- Triggering the panic requires a scenario where the aggregated entry becomes stale/inconsistent by the time of `finalize_block` — plausible only through the concurrent execution path (`worker_logic`/`ConcurrentTransactionExecutor`) where multiple transactions can read/build migration data for the same class hash at different points in a block, and the migration map is a HashMap `insert` (last-write-wins) merged via `extend`, not validated against the actual final class hash-to-compiled-class-hash state before being applied.
- I could not fully verify from static reading alone whether the concurrency model guarantees per-class-hash migration data staleness cannot occur (e.g., whether `try_update`'s "marginal" computation could produce two different `(v2,v1)` pairs for the same class hash from different transactions, one of which becomes invalid after commit ordering). This would need dynamic/concurrency testing to confirm exploitability with certainty.
- The `?`-propagated `StateError` path (rather than the panic) is more directly reachable but its practical trigger (e.g., an `UndeclaredClassHash` state error at write time) is not clearly reachable by an attacker without further investigation into `CachedState::set_compiled_class_hash`.

Given this uncertainty, I flag this as a **Medium**-confidence architectural analog: the aggregation pattern is structurally identical to the reported bug class (unchecked per-item aggregation feeding a single all-or-nothing operation reachable from unprivileged transactions), but I was not able to construct a concrete, deterministic single-transaction PoC purely from code inspection.

### Recommendation
- Isolate per-class-hash migration failures: instead of `?`/`assert_ne!` aborting the whole `set_compiled_class_hash_migration` loop, skip (and log/metric) any migration entry that fails its invariant or state write, without failing the entire block.
- Re-validate each migration entry against the state's *final* (post all committed txs) compiled-class-hash right before writing, rather than trusting entries accumulated from potentially-stale marginal per-transaction reads.
- Add regression tests that concurrently execute multiple transactions referencing the same class hash under `enable_casm_hash_migration = true` to confirm no interleaving can produce a `v1 == v2` migration entry or a state write error at `finalize_block` time.

### Proof of Concept
Not reproduced as a concrete end-to-end PoC. The analysis is based on static code tracing of the aggregation path: `should_migrate` → `CasmHashMigrationData::from_state` (per-tx) → `Bouncer.accumulated_weights.class_hashes_to_migrate.extend(...)` (per-tx, block-scoped aggregation) → `finalize_block` → `set_compiled_class_hash_migration` (all-txs, single pass with `assert_ne!` + `?`). Confirming actual triggerability under the `ConcurrentTransactionExecutor`/`worker_logic` commit ordering would require a dynamic concurrency test, which was not performed here.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L377-400)
```rust
    fn from_state<S: StateReader>(
        state_reader: &S,
        executed_class_hashes: &HashSet<ClassHash>,
        versioned_constants: &VersionedConstants,
    ) -> TransactionExecutionResult<Self> {
        if !versioned_constants.enable_casm_hash_migration {
            return Ok(Self::empty());
        }

        executed_class_hashes.iter().try_fold(Self::empty(), |mut migration_data, &class_hash| {
            if let Some((class_hash, casm_hash_v2_to_v1)) =
                should_migrate(state_reader, class_hash)?
            {
                // Add class hash mapping to the migration data.
                migration_data.class_hashes_to_migrate.insert(class_hash, casm_hash_v2_to_v1);

                // Accumulate the class's migration resources.
                let class = state_reader.get_compiled_class(class_hash)?;
                migration_data.resources +=
                    &class.estimate_compiled_class_hash_migration_resources();
            }
            Ok(migration_data)
        })
    }
```

**File:** crates/blockifier/src/bouncer.rs (L708-726)
```rust
        self.accumulated_weights.bouncer_weights = self
            .accumulated_weights
            .bouncer_weights
            .checked_add(tx_weights.bouncer_weights)
            .expect(&err_msg);
        self.accumulated_weights
            .casm_hash_computation_data_sierra_gas
            .extend(tx_weights.casm_hash_computation_data_sierra_gas);
        self.accumulated_weights
            .casm_hash_computation_data_proving_gas
            .extend(tx_weights.casm_hash_computation_data_proving_gas);
        self.visited_storage_entries.extend(&tx_execution_summary.visited_storage_entries);
        // Note: cancelling writes (0 -> 1 -> 0) will not be removed, but it's fine since fee was
        // charged for them.
        // Also, `get_patricia_update_resources` relies on this property - each cell must
        // be counted at most once as modified.
        self.state_changes_keys.extend(state_changes_keys);
        self.accumulated_weights.class_hashes_to_migrate.extend(tx_weights.class_hashes_to_migrate);
    }
```

**File:** crates/blockifier/src/utils.rs (L119-143)
```rust
// Class should migrate if his compiled class hash v2 is different from the one in the state.
/// Returns a map of class hashes to their compiled class hashes for migration if the class should
/// migrate, otherwise returns None.
pub fn should_migrate(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
) -> StateResult<Option<(ClassHash, CompiledClassHashV2ToV1)>> {
    let state_compiled_class_hash = state_reader.get_compiled_class_hash(class_hash)?;
    match state_compiled_class_hash {
        // Class hash does not exist in the state, or is a Cairo 0 class.
        CompiledClassHash(hash) if hash == StarkHash::ZERO => Ok(None),
        state_compiled_class_hash => {
            let compiled_class_hash_v2 = state_reader.get_compiled_class_hash_v2(
                class_hash,
                &state_reader.get_compiled_class(class_hash)?,
            )?;
            // If the state compiled class hash is compiled class hash v2, the class should not
            // migrate.
            if state_compiled_class_hash == compiled_class_hash_v2 {
                return Ok(None);
            }
            Ok(Some((class_hash, (compiled_class_hash_v2, state_compiled_class_hash))))
        }
    }
}
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L256-271)
```rust
    let class_hashes_to_migrate = mem::take(bouncer.get_mut_class_hashes_to_migrate());
    #[cfg(any(test, feature = "testing"))]
    if !class_hashes_to_migrate.is_empty() {
        log::info!(
            "Class hashes to migrate (key = class_hash, value = (compiled_class_hash_v2, \
             compiled_class_hash_v1)): {class_hashes_to_migrate:#?}"
        );
    }

    if !block_context.versioned_constants.enable_casm_hash_migration {
        assert!(
            class_hashes_to_migrate.is_empty(),
            "Class hashes to migrate should be empty when migration is disabled"
        );
    }
    block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;
```

**File:** crates/blockifier/src/state/compiled_class_hash_migration.rs (L16-36)
```rust
impl<S: StateReader> CompiledClassHashMigrationUpdater for CachedState<S> {
    // Sets the new compiled class hashes for the class hashes that need to be migrated.
    fn set_compiled_class_hash_migration(
        &mut self,
        class_hashes_to_migrate: &HashMap<ClassHash, CompiledClassHashV2ToV1>,
    ) -> StateResult<()> {
        for (class_hash, (compiled_class_hash_v2, compiled_class_hash_v1)) in
            class_hashes_to_migrate
        {
            // Sanity check: the compiled class hashes should not be equal.
            assert_ne!(
                compiled_class_hash_v1, compiled_class_hash_v2,
                "Classes for migration should hold v1 (Poseidon) hash in the state."
            );

            // TODO(Meshi): Consider panic here instead of returning an error.
            self.set_compiled_class_hash(*class_hash, *compiled_class_hash_v2)?;
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/blockifier/concurrent_transaction_executor.rs (L124-146)
```rust
    pub fn close_block(
        &mut self,
        final_n_executed_txs: usize,
    ) -> TransactionExecutorResult<BlockExecutionSummary> {
        log::info!("Worker executor: Closing block.");
        let worker_executor = &self.worker_executor;
        worker_executor.scheduler.halt();

        let n_committed_txs = worker_executor.scheduler.get_n_committed_txs();
        assert!(
            final_n_executed_txs <= n_committed_txs,
            "Close block requested with {final_n_executed_txs} transactions, but only \
             {n_committed_txs} transactions were committed."
        );

        let mut state_after_block =
            worker_executor.commit_chunk_and_recover_block_state(final_n_executed_txs);
        finalize_block(
            &worker_executor.bouncer,
            &mut state_after_block,
            &self.worker_executor.block_context,
        )
    }
```

**File:** crates/apollo_batcher/src/transaction_executor.rs (L24-31)
```rust
    /// Finalizes the block creation and returns the commitment state diff, visited
    /// segments mapping and bouncer.
    ///
    /// Every block must be closed with either `close_block` or `abort_block`.
    fn close_block(
        &mut self,
        final_n_executed_txs: usize,
    ) -> TransactionExecutorResult<BlockExecutionSummary>;
```

**File:** crates/apollo_batcher/src/block_builder.rs (L85-103)
```rust
#[derive(Debug, Error)]
pub enum BlockBuilderError {
    #[error(transparent)]
    BlockifierStateError(#[from] StateError),
    #[error(transparent)]
    ExecutorError(#[from] BlockifierTransactionExecutorError),
    #[error(transparent)]
    GetTransactionError(#[from] TransactionProviderError),
    #[error(transparent)]
    StreamTransactionsError(
        #[from] Box<tokio::sync::mpsc::error::SendError<InternalConsensusTransaction>>,
    ),
    #[error(transparent)]
    FailOnError(FailOnErrorCause),
    #[error("The block builder was aborted.")]
    Aborted,
    #[error(transparent)]
    TransactionConverterError(#[from] TransactionConverterError),
}
```
