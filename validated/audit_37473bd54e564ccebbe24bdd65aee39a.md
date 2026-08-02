Based on the investigation, the strongest local analog to the reported bug class ("code assumes an operation cannot fail, but it silently can, causing state to be permanently lost/corrupted, invisible to retries") is in the Aptos backup/restore state-commit path.

### Title
KV-replay during restore can silently skip state-tree updates while still advancing ledger commit progress, permanently orphaning state values for a version range - (File: `storage/aptosdb/src/backup/restore_utils.rs`)

### Summary
`save_transactions_impl` gates the state-tree/state-KV update step behind `state_store.get_usage(Some(first_version - 1)).is_ok()`, silently skipping `calculate_state_and_put_updates` when the check fails, but unconditionally still commits transactions, transaction infos, the accumulator, write sets, and advances `LedgerCommitProgress`/`OverallCommitProgress` to `last_version`.

### Finding Description
In `storage/aptosdb/src/backup/restore_utils.rs`, `save_transactions_impl` writes transactions, `PersistedAuxiliaryInfo`, `TransactionInfo`, accumulator leaves, events, and write sets unconditionally [1](#0-0) . It then only recomputes and persists the actual state values / state tree (`calculate_state_and_put_updates`) when `kv_replay` is set **and** `state_store.get_usage(Some(first_version - 1)).is_ok()`: [2](#0-1) 

`get_usage` looks up `VersionDataSchema` for the requested version and returns `Err` when it's absent — confirmed by the unit test `assert!(ledger_metadata_db.get_usage(0).is_err())` [3](#0-2) . `VersionData`/usage entries are only populated by `put_usage`, called from `put_stats_and_indices`, which itself is only invoked from the ordinary execution/state-update path (`put_state_updates` → `put_stats_and_indices`) [4](#0-3) , not from a bare state-snapshot restore.

Immediately after `save_transactions_impl`'s state-tree branch is skipped (or run), the function unconditionally writes `LedgerCommitProgress` and `OverallCommitProgress` up to `last_version`: [5](#0-4) 

This code path is exercised by the restore coordinator specifically in the scenario where a KV snapshot is restored and then a transaction range is KV-replayed starting right after it, precisely to "get correct storage usage at that version while restoring tree-only snapshots": [6](#0-5) 

Because a state-snapshot restore populates raw state values / JMT nodes but not necessarily the `VersionDataSchema` usage record at the snapshot version, the very first `get_usage(Some(first_version - 1))` call in the subsequent KV-replay chunk (`first_version - 1` == the just-restored snapshot version) can return `Err`. In that case the branch that computes and persists state values/usage for the chunk (`calculate_state_and_put_updates` → `put_state_values`) never runs, so the actual state K/V and state-tree data for that chunk's versions is never written — while `WriteSetDb`, transaction infos, and the accumulator are written, and `LedgerCommitProgress`/`OverallCommitProgress` are advanced past those versions as if the commit fully succeeded.

### Impact Explanation
Once `OverallCommitProgress`/`LedgerCommitProgress` are advanced past a version, restore/replay bookkeeping (`get_next_expected_transaction_version`, dedup/skip logic) treats that version as done and will not revisit it. The result is a durable, silent divergence between what the ledger accumulator/`TransactionInfo` (and its `state_checkpoint_hash`) claim for those versions and what the actual persisted state tree/state KV DB contains for the same versions — i.e., committed ledger data that does not reflect the correct state. Any subsequent authenticated state read (`get_state_value_with_proof_by_version`) or replay for that version range will either return missing/incorrect values or fail proof verification against the (unrelated) recorded `state_checkpoint_hash`, and there is no automatic retry path since the progress markers say the range is already committed. This falls squarely in the "committed state differs from the correct VM result / corrupts durable ledger data" and "wrong state proof accepted/rejected due to version binding" impact categories.

### Likelihood Explanation
This requires the specific restore combination of an existing KV snapshot followed by KV-replay of a subsequent transaction range, which is a real, used code path (`storage/backup/backup-cli/src/coordinators/restore.rs`), triggered by any operator or node performing a fast/tree-only DB restore. I was not able to fully confirm from the index whether the state-snapshot restore code (`storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs`) writes a `VersionData`/usage entry at the snapshot version through some other path I couldn't inspect (the file's content was largely excluded from the index — only one unrelated match for "usage" was found), so I cannot rule out that some other component populates this record before the KV-replay step runs. This is the key uncertainty: if usage is populated elsewhere at snapshot-restore time, the described skip would not actually occur in practice.

### Recommendation
- Make the `get_usage` check a hard invariant: if `kv_replay` is requested but the baseline usage is unavailable, return an error instead of silently skipping the state-tree computation.
- Do not advance `LedgerCommitProgress`/`OverallCommitProgress` in `save_transactions_impl` unless the corresponding state-tree/state-KV update actually ran (when `kv_replay` is true), so progress can never outrun state materialization.
- Add an explicit `put_usage` write (or equivalent baseline) as part of state-snapshot restore completion, so that any downstream KV-replay's baseline lookup is guaranteed to succeed.

### Proof of Concept
Could not be fully constructed/verified within the index due to missing visibility into `storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs`'s completion logic (file content mostly excluded from the index). A concrete PoC would need to: (1) create a DB, take a state (KV) snapshot at version V, (2) restore into a fresh DB using `StateSnapshotRestoreController` with `StateSnapshotRestoreMode::KvOnly`, (3) immediately call `TransactionRestoreController`/`save_transactions` with `kv_replay = true` starting at `V+1`, and (4) check whether `state_store.get_usage(Some(V))` succeeds and whether state values in `StateValueByKeyHashSchema` are present for the replayed range while `OverallCommitProgress` has advanced past it. I recommend running this scenario in a Devin session with full repository access to confirm the concrete corrupted value and finalize the PoC, given the index's file-size limits prevented full verification here.

### Citations

**File:** storage/aptosdb/src/backup/restore_utils.rs (L206-265)
```rust
    for (idx, txn) in txns.iter().enumerate() {
        ledger_db.transaction_db().put_transaction(
            first_version + idx as Version,
            txn,
            /*skip_index=*/ false,
            &mut ledger_db_batch.transaction_db_batches,
        )?;
    }

    for (idx, aux_info) in persisted_aux_info.iter().enumerate() {
        PersistedAuxiliaryInfoDb::put_persisted_auxiliary_info(
            first_version + idx as Version,
            aux_info,
            &mut ledger_db_batch.persisted_auxiliary_info_db_batches,
        )?;
    }

    for (idx, txn_info) in txn_infos.iter().enumerate() {
        TransactionInfoDb::put_transaction_info(
            first_version + idx as Version,
            txn_info,
            &mut ledger_db_batch.transaction_info_db_batches,
        )?;
    }

    ledger_db
        .transaction_accumulator_db()
        .put_transaction_accumulator(
            first_version,
            txn_infos,
            &mut ledger_db_batch.transaction_accumulator_db_batches,
        )?;

    ledger_db.event_db().put_events_multiple_versions(
        first_version,
        events,
        &mut ledger_db_batch.event_db_batches,
    )?;

    for (idx, txn_events) in events.iter().enumerate() {
        for event in txn_events {
            if let Some(event_key) = event.event_key() {
                if *event_key == new_block_event_key() {
                    LedgerMetadataDb::put_block_info(
                        first_version + idx as Version,
                        event,
                        &mut ledger_db_batch.ledger_metadata_db_batches,
                    )?;
                }
            }
        }
    }
    // insert changes in write set schema batch
    for (idx, ws) in write_sets.iter().enumerate() {
        WriteSetDb::put_write_set(
            first_version + idx as Version,
            ws,
            &mut ledger_db_batch.write_set_db_batches,
        )?;
    }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L267-275)
```rust
    if kv_replay && first_version > 0 && state_store.get_usage(Some(first_version - 1)).is_ok() {
        let (ledger_state, _hot_state_updates) = state_store.calculate_state_and_put_updates(
            &StateUpdateRefs::index_write_sets(first_version, write_sets, write_sets.len(), vec![]),
            &mut ledger_db_batch.ledger_metadata_db_batches, // used for storing the storage usage
            state_kv_batches,
        )?;
        // n.b. ideally this is set after the batches are committed
        state_store.set_state_ignoring_summary(ledger_state);
    }
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L277-292)
```rust
    let last_version = first_version + txns.len() as u64 - 1;
    ledger_db_batch
        .ledger_metadata_db_batches
        .put::<DbMetadataSchema>(
            &DbMetadataKey::LedgerCommitProgress,
            &DbMetadataValue::Version(last_version),
        )?;
    ledger_db_batch
        .ledger_metadata_db_batches
        .put::<DbMetadataSchema>(
            &DbMetadataKey::OverallCommitProgress,
            &DbMetadataValue::Version(last_version),
        )?;

    Ok(())
}
```

**File:** storage/aptosdb/src/ledger_db/ledger_metadata_db_test.rs (L328-339)
```rust
#[test]
fn test_usage() {
    let tmp_dir = TempPath::new();
    let db = AptosDB::new_for_test(&tmp_dir);
    let ledger_metadata_db = db.ledger_db.metadata_db();

    let usage = StateStorageUsage::new(7, 23);
    ledger_metadata_db.put_usage(1, usage).unwrap();
    assert_eq!(ledger_metadata_db.get_usage(1).unwrap(), usage);
    assert!(ledger_metadata_db.get_usage(0).is_err());
}

```

**File:** storage/aptosdb/src/state_store/mod.rs (L1111-1132)
```rust
    pub fn put_state_updates(
        &self,
        state: &LedgerState,
        state_update_refs: &PerVersionStateUpdateRefs,
        state_reads: &ShardedStateCache,
        ledger_batch: &mut SchemaBatch,
        sharded_state_kv_batches: &mut ShardedStateKvSchemaBatch,
    ) -> Result<()> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["put_value_sets"]);
        let current_state = self.current_state_locked().state().clone();

        self.put_stats_and_indices(
            &current_state,
            state,
            state_update_refs,
            state_reads,
            ledger_batch,
            sharded_state_kv_batches,
        )?;

        self.put_state_values(state_update_refs, sharded_state_kv_batches)
    }
```

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L278-300)
```rust
            // update the kv to the kv db
            // reset the global
            let mut transaction_restore_opt = self.global_opt.clone();
            // We should replay kv to include the version of tree snapshot so that we can get correct storage usage at that version
            // while restore tree only snapshots
            let kv_replay_version = if let Some(kv_snapshot) = kv_snapshot.as_ref() {
                kv_snapshot.version + 1
            } else {
                db_next_version
            };
            transaction_restore_opt.target_version = tree_snapshot.version;
            TransactionRestoreBatchController::new(
                transaction_restore_opt,
                Arc::clone(&self.storage),
                txn_manifests,
                Some(db_next_version),
                Some((kv_replay_version, true /* only replay KV */)),
                epoch_history.clone(),
                VerifyExecutionMode::NoVerify,
                None,
            )
            .run()
            .await?;
```
