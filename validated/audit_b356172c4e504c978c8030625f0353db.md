## Title
Non-atomic frozen-subtree write in `finalize_state_snapshot` can desynchronize the transaction accumulator from ledger commit progress - (File: storage/aptosdb/src/db/aptosdb_writer.rs)

### Summary
`DbWriter::finalize_state_snapshot` (used by fast-sync / state-sync-v2 restore) writes the transaction accumulator's frozen-subtree roots to `transaction_accumulator_db_raw()` immediately and outside of the atomic `ledger_db_batch` that carries the rest of the version's data and the `LedgerCommitProgress`/`OverallCommitProgress` markers. This mirrors the external report's root cause: a partial, unmanaged side-effect (the "leftover collateral") is left behind because a value that should be committed atomically together with the rest of the operation is instead applied through an out-of-band path, with no compensating cleanup if the remainder of the operation does not complete.

### Finding Description
In `finalize_state_snapshot`: [1](#0-0) 

the code explicitly flags this as unfinished work ("TODO(joshlind): include confirm_or_save_frozen_subtrees in the change set bundle below"), and immediately calls `restore_utils::confirm_or_save_frozen_subtrees(..., None)`, which — because `existing_batch` is `None` — builds its own `SchemaBatch` and calls `transaction_accumulator_db.write_schemas(batch)` directly: [2](#0-1) 

This write happens and is durably committed to the `transaction_accumulator_db` **before** the subsequent, separate atomic batch (`ledger_db_batch`, containing transaction info, events, write sets, and — critically — the `DbMetadataKey::LedgerCommitProgress` / `DbMetadataKey::OverallCommitProgress` markers) is written via `self.ledger_db.write_schemas(ledger_db_batch)`: [3](#0-2) 

If the process crashes/restarts between these two writes, the frozen-subtree roots for `version` are already persisted in the accumulator column family, but no commit-progress marker reflects that this version was finalized. The recovery path (`sync_commit_progress`) uses `LedgerCommitProgress`/`OverallCommitProgress` as "the source of truth of the commit progress" and truncates the ledger DB back to that value: [4](#0-3) 

Because the progress marker was never advanced, the recovery logic has no signal telling it that accumulator rows for `version` were already written out-of-band, so it does not know it needs to truncate/reconcile them. On a subsequent retry of `finalize_state_snapshot` for the same `version` (which state-sync driving code is expected to do after a restart), `confirm_or_save_frozen_subtrees` is invoked again; its `confirm_or_save` semantics assume it is either the first write for these positions or that the previously-saved values must match. If any accumulator position rows were left over from the earlier partial/aborted attempt but the retried transaction info/proof used to compute `frozen_subtrees` differs (e.g., after a different backup source, or a differing genesis/epoch chunk selection), the "confirm" check against stale on-disk data can fail or (depending on the specific proof left-siblings ordering assumptions) silently accept mismatched roots, producing a transaction accumulator whose frozen subtree roots do not correspond to the transaction infos that get committed in the same call. This breaks the invariant that the transaction accumulator root and the ledger's transaction info/write-set data reaching storage must be updated as a single atomic unit.

### Impact Explanation
The transaction accumulator underpins every Merkle proof for transaction inclusion (`TransactionAccumulatorProof`) and the ledger-info-to-transaction-info binding used by clients and light nodes to authenticate ledger state. If frozen-subtree roots are committed independently of, and out of sync with, the transaction infos/commit-progress for the same version, a restarted node can end up with an accumulator whose root does not match what the corresponding `LedgerInfo`/proof material implies, or with orphaned subtree entries that get reused incorrectly on retry. This is a state-commitment/proof-integrity defect: a wrong accumulator root or an inconsistent restore state could be durably persisted, which is squarely in the "wrong accumulator root ... accepted as valid" / "restore paths must preserve deterministic proof binding" category from the state-integrity gate.

### Likelihood Explanation
This path is only exercised during the fast-sync / state-snapshot-restore (`finalize_state_snapshot`) flow, which itself is triggered by state-sync-v2/backup-restore logic on any node bootstrapping from a snapshot — not an attacker-controlled or privileged-only code path, so it is reachable by ordinary node operation (e.g., any node crash/restart during a fast sync). However, triggering the exact inconsistent-recommit scenario requires a crash precisely between the two writes, which is a narrow timing window, and I was not able to fully verify (within the available tool budget) whether `confirm_or_save_frozen_subtrees_impl`'s "confirm" comparison would actually reject vs. silently accept a divergent retry, nor how `truncate_ledger_db` treats the transaction-accumulator column family specifically on restart. This uncertainty means the finding should be treated as a proven code smell/atomicity gap with a plausible corruption path, rather than a fully demonstrated end-to-end root-hash mismatch.

### Recommendation
Move the `confirm_or_save_frozen_subtrees` write into the same `ledger_db_batch` that is committed atomically later in `finalize_state_snapshot`, exactly as the existing `// TODO(joshlind)` comment states, so the frozen-subtree roots, transaction infos/events/write-sets, and the `LedgerCommitProgress`/`OverallCommitProgress` markers are all persisted (or all rolled back on crash) as a single atomic unit. Additionally, verify that `sync_commit_progress`/`truncate_ledger_db` correctly reconcile the transaction-accumulator CF on restart if any out-of-band writes remain from older binaries, to prevent orphaned frozen-subtree state on upgrade.

### Proof of Concept
Conceptual crash-window PoC (not independently executed given ask-only/no-terminal access):
1. Start a fast-sync/backup restore that calls `DbWriter::finalize_state_snapshot(version, ...)`.
2. Let the call proceed through `restore_utils::confirm_or_save_frozen_subtrees(...)` (line 175-180 of `aptosdb_writer.rs`), which commits directly to `transaction_accumulator_db_raw()`.
3. Kill the process before `self.ledger_db.write_schemas(ledger_db_batch)` executes (line 243).
4. On restart, `LedgerCommitProgress`/`OverallCommitProgress` still reflect the pre-snapshot version, but the transaction-accumulator CF already contains frozen-subtree rows for `version`.
5. Re-run the snapshot restore for the same `version` and inspect whether the resulting accumulator root, once fully committed, matches the expected `LedgerInfo` root, or whether `confirm_or_save_frozen_subtrees_impl` raises a mismatch/`ensure!` failure indicating stale, unmanaged data from the aborted attempt. [5](#0-4)

### Citations

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L145-264)
```rust
    fn finalize_state_snapshot(
        &self,
        version: Version,
        output_with_proof: TransactionOutputListWithProofV2,
        ledger_infos: &[LedgerInfoWithSignatures],
    ) -> Result<()> {
        let (output_with_proof, persisted_aux_info) = output_with_proof.into_parts();
        gauged_api("finalize_state_snapshot", || {
            // Ensure the output with proof only contains a single transaction output and info
            let num_transaction_outputs = output_with_proof.get_num_outputs();
            let num_transaction_infos = output_with_proof.proof.transaction_infos.len();
            ensure!(
                num_transaction_outputs == 1,
                "Number of transaction outputs should == 1, but got: {}",
                num_transaction_outputs
            );
            ensure!(
                num_transaction_infos == 1,
                "Number of transaction infos should == 1, but got: {}",
                num_transaction_infos
            );

            // TODO(joshlind): include confirm_or_save_frozen_subtrees in the change set
            // bundle below.

            // Update the merkle accumulator using the given proof
            let frozen_subtrees = output_with_proof
                .proof
                .ledger_info_to_transaction_infos_proof
                .left_siblings();
            restore_utils::confirm_or_save_frozen_subtrees(
                self.ledger_db.transaction_accumulator_db_raw(),
                version,
                frozen_subtrees,
                None,
            )?;

            // Create a single change set for all further write operations
            let mut ledger_db_batch = LedgerDbSchemaBatches::new();
            let mut sharded_kv_batch = self.state_kv_db.new_sharded_native_batches();
            let mut state_kv_metadata_batch = SchemaBatch::new();
            // Save the target transactions, outputs, infos and events
            let (transactions, outputs): (Vec<Transaction>, Vec<TransactionOutput>) =
                output_with_proof
                    .transactions_and_outputs
                    .into_iter()
                    .unzip();
            let events = outputs
                .clone()
                .into_iter()
                .map(|output| output.events().to_vec())
                .collect::<Vec<_>>();
            let wsets: Vec<WriteSet> = outputs
                .into_iter()
                .map(|output| output.write_set().clone())
                .collect();
            let transaction_infos = output_with_proof.proof.transaction_infos;
            // We should not save the key value since the value is already recovered for this version
            restore_utils::save_transactions(
                self.state_store.clone(),
                self.ledger_db.clone(),
                version,
                &transactions,
                &persisted_aux_info,
                &transaction_infos,
                &events,
                wsets,
                Some((
                    &mut ledger_db_batch,
                    &mut sharded_kv_batch,
                    &mut state_kv_metadata_batch,
                )),
                false,
            )?;

            // Save the epoch ending ledger infos
            restore_utils::save_ledger_infos(
                self.ledger_db.metadata_db(),
                ledger_infos,
                Some(&mut ledger_db_batch.ledger_metadata_db_batches),
            )?;

            ledger_db_batch
                .ledger_metadata_db_batches
                .put::<DbMetadataSchema>(
                    &DbMetadataKey::LedgerCommitProgress,
                    &DbMetadataValue::Version(version),
                )?;
            ledger_db_batch
                .ledger_metadata_db_batches
                .put::<DbMetadataSchema>(
                    &DbMetadataKey::OverallCommitProgress,
                    &DbMetadataValue::Version(version),
                )?;

            // Apply the change set writes to the database (atomically) and update in-memory state
            //
            // state kv and SMT should use shared way of committing.
            self.ledger_db.write_schemas(ledger_db_batch)?;

            self.ledger_pruner.save_min_readable_version(version)?;
            self.state_store
                .state_pruner
                .state_merkle_pruner
                .save_min_readable_version(version)?;
            self.state_store
                .state_pruner
                .epoch_snapshot_pruner
                .save_min_readable_version(version)?;
            self.state_store
                .state_pruner
                .state_kv_pruner
                .save_min_readable_version(version)?;

            restore_utils::update_latest_ledger_info(self.ledger_db.metadata_db(), ledger_infos)?;
            self.state_store.reset();

            Ok(())
        })
    }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L78-111)
```rust
pub fn confirm_or_save_frozen_subtrees(
    transaction_accumulator_db: &DB,
    num_leaves: LeafCount,
    frozen_subtrees: &[HashValue],
    existing_batch: Option<&mut SchemaBatch>,
) -> Result<()> {
    let positions: Vec<_> = FrozenSubTreeIterator::new(num_leaves).collect();
    ensure!(
        positions.len() == frozen_subtrees.len(),
        "Number of frozen subtree roots not expected. Expected: {}, actual: {}",
        positions.len(),
        frozen_subtrees.len(),
    );

    if let Some(existing_batch) = existing_batch {
        confirm_or_save_frozen_subtrees_impl(
            transaction_accumulator_db,
            frozen_subtrees,
            positions,
            existing_batch,
        )?;
    } else {
        let mut batch = SchemaBatch::new();
        confirm_or_save_frozen_subtrees_impl(
            transaction_accumulator_db,
            frozen_subtrees,
            positions,
            &mut batch,
        )?;
        transaction_accumulator_db.write_schemas(batch)?;
    }

    Ok(())
}
```

**File:** storage/aptosdb/src/state_store/mod.rs (L526-569)
```rust
    // We commit the overall commit progress at the last, and use it as the source of truth of the
    // commit progress.
    pub fn sync_commit_progress(
        ledger_db: Arc<LedgerDb>,
        state_kv_db: Arc<StateKvDb>,
        state_merkle_db: Arc<StateMerkleDb>,
        hot_state_kv_db: Arc<StateKvDb>,
        hot_state_merkle_db: Arc<StateMerkleDb>,
        crash_if_difference_is_too_large: bool,
        delete_hot_state_on_restart: bool,
    ) {
        let ledger_metadata_db = ledger_db.metadata_db();
        let Some(overall_commit_progress) = ledger_metadata_db
            .get_synced_version()
            .expect("DB read failed.")
        else {
            // Theoretically someone could bring up a new node and it could crash after saving the
            // genesis transaction and before writing overall commit progress. Probably not worth
            // fixing right now.
            info!("No overall commit progress was found!");
            return;
        };

        info!(
            overall_commit_progress = overall_commit_progress,
            "Start syncing databases..."
        );
        let ledger_commit_progress = ledger_metadata_db
            .get_ledger_commit_progress()
            .expect("Failed to read ledger commit progress.");
        assert_ge!(ledger_commit_progress, overall_commit_progress);

        // LedgerCommitProgress was not guaranteed to commit after all ledger changes finish,
        // have to attempt truncating every column family.
        info!(
            ledger_commit_progress = ledger_commit_progress,
            "Attempt ledger truncation...",
        );
        let difference = ledger_commit_progress - overall_commit_progress;
        if crash_if_difference_is_too_large {
            assert_le!(difference, MAX_COMMIT_PROGRESS_DIFFERENCE);
        }
        truncate_ledger_db(ledger_db.clone(), overall_commit_progress)
            .expect("Failed to truncate ledger db.");
```
