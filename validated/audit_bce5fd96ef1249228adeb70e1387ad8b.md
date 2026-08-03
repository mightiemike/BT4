[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L50-83)
```rust
    fn pre_commit_ledger(&self, chunk: ChunkToCommit, sync_commit: bool) -> Result<()> {
        gauged_api("pre_commit_ledger", || {
            // Pre-committing and committing in concurrency is allowed but not pre-committing at the
            // same time from multiple threads, the same for committing.
            // Consensus and state sync must hand over to each other after all pending execution and
            // committing complete.
            let _lock = self
                .pre_commit_lock
                .try_lock()
                .expect("Concurrent committing detected.");
            let _timer = OTHER_TIMERS_SECONDS.timer_with(&["pre_commit_ledger"]);

            chunk
                .state_summary
                .latest()
                .global_state_summary
                .log_generation("db_save");

            self.pre_commit_validation(&chunk)?;
            let _new_root_hash =
                self.calculate_and_commit_ledger_and_state_kv(&chunk, sync_commit)?;

            let _timer = OTHER_TIMERS_SECONDS.timer_with(&["save_transactions__others"]);

            self.state_store.buffered_state().lock().update(
                chunk.result_ledger_state_with_summary(),
                chunk.hot_state_updates.clone(),
                chunk.estimated_total_state_updates(),
                sync_commit || chunk.is_reconfig,
            )?;

            Ok(())
        })
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L585-632)
```rust
    pub(super) fn commit_transaction_accumulator(
        &self,
        first_version: Version,
        transaction_infos: &[TransactionInfo],
    ) -> Result<HashValue> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_transaction_accumulator"]);

        let num_txns = transaction_infos.len() as Version;

        let mut batch = SchemaBatch::new();
        let root_hash = self
            .ledger_db
            .transaction_accumulator_db()
            .put_transaction_accumulator(first_version, transaction_infos, &mut batch)?;

        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_transaction_accumulator___commit"]);
        self.ledger_db
            .transaction_accumulator_db()
            .write_schemas(batch)?;

        let mut batch = SchemaBatch::new();
        let all_versions: Vec<_> = (first_version..first_version + num_txns).collect();
        THREAD_MANAGER
            .get_non_exe_cpu_pool()
            .install(|| -> Result<()> {
                let all_root_hashes = all_versions
                    .into_par_iter()
                    .with_min_len(64)
                    .map(|version| {
                        self.ledger_db
                            .transaction_accumulator_db()
                            .get_root_hash(version)
                    })
                    .collect::<Result<Vec<_>>>()?;
                all_root_hashes
                    .iter()
                    .enumerate()
                    .try_for_each(|(i, hash)| {
                        let version = first_version + i as u64;
                        batch.put::<TransactionAccumulatorRootHashSchema>(&version, hash)
                    })?;
                self.ledger_db
                    .transaction_accumulator_db()
                    .write_schemas(batch)
            })?;

        Ok(root_hash)
    }
```

**File:** storage/storage-interface/src/chunk_to_commit.rs (L18-34)
```rust
pub struct ChunkToCommit<'a> {
    pub first_version: Version,
    pub transactions: &'a [Transaction],
    pub persisted_auxiliary_infos: &'a [PersistedAuxiliaryInfo],
    pub transaction_outputs: &'a [TransactionOutput],
    pub transaction_infos: &'a [TransactionInfo],
    pub state: &'a LedgerState,
    pub state_summary: &'a LedgerStateSummary,
    pub state_update_refs: &'a StateUpdateRefs<'a>,
    pub state_reads: &'a ShardedStateCache,
    pub hot_state_updates: &'a HotStateUpdates,
    /// Position summary computed at execution time (checkpoint stage), to
    /// be persisted/merklized at commit without recomputation. `None` when
    /// native position is disabled.
    pub position_state_summary: Option<&'a LedgerWithSummary<PositionStateWithSummary>>,
    pub is_reconfig: bool,
}
```
