### Title
`kv_replay` state-index skip in restore leaves committed ledger progress inconsistent with the JMT/state-KV write set - ([File: storage/aptosdb/src/backup/restore_utils.rs])

### Summary
In `save_transactions_impl`, when `kv_replay` is requested, the state store's JMT/state-KV index is only updated if `state_store.get_usage(Some(first_version - 1)).is_ok()`. If this lookup fails, the function silently skips `calculate_state_and_put_updates` (and thus `set_state_ignoring_summary`) for the whole chunk, yet it unconditionally advances `LedgerCommitProgress` and `OverallCommitProgress` to `last_version` a few lines later.

### Finding Description
`save_transactions_impl` writes transactions, txn infos, the accumulator, events, and raw write sets for `[first_version, last_version]` unconditionally. [1](#0-0) 

State (JMT) indexing is conditionally gated: [2](#0-1) 

Then, regardless of whether the state-indexing branch ran, the commit-progress markers are advanced to `last_version`: [3](#0-2) 

The condition guarding state calculation combines three independent checks with `&&`: `kv_replay`, `first_version > 0`, and `state_store.get_usage(Some(first_version - 1)).is_ok()`. The comment in `RestoreCoordinator::run_impl` explains the intent of `kv_replay` — it is turned on specifically in phase 1.b to replay write sets into state-KV/JMT so that `StateStorageUsage` can be computed correctly at the tree-snapshot version: [4](#0-3) [5](#0-4) 

If `get_usage(Some(first_version - 1))` returns `Err` (e.g., because the prior version's usage record is not yet present due to restore ordering, pruning, or a resumed/partial restore), the code treats this as a silent "do nothing" case rather than propagating an error. Because the surrounding call site (`save_transactions`) does not re-check whether the state branch actually executed, and because `LedgerCommitProgress`/`OverallCommitProgress` are advanced unconditionally right after, the restore path will believe versions `[first_version, last_version]` are fully committed (transactions, txn-infos, accumulator, write sets) while the corresponding state-KV/JMT entries for those versions were never derived from the write sets. Since restore resumption logic in `RestoreCoordinator` (`db_next_version`) is driven by these very progress markers, a resumed or interrupted restore run has no future opportunity to re-derive the skipped state index for that version range — the gap is permanent.

### Impact Explanation
This breaks the storage/proof-integrity invariant "restore helpers must not reinterpret committed data into a different ledger state" and "accumulators, JMT structures ... must preserve deterministic proof binding." A restored full node whose state tree silently diverges from the true post-write-set state for a version range would serve state values, state proofs, and account/resource reads for that range that are inconsistent with the actual committed write sets (transactions/txn-infos/write-sets say one thing, the JMT says something stale/absent). Any authenticated API response backed by that state tree (state proof, resource read, JMT-based sync target) for the affected keys/version would be wrong or fail JMT-consistency checks against the ledger info's expected state root, which is a high-severity proof-integrity issue for restore-based nodes/backup consumers on mainnet.

### Likelihood Explanation
This requires `get_usage(Some(first_version - 1))` to fail during a `kv_replay=true` chunk — this is plausible in interrupted/resumed restores or non-trivial restore topologies (the code comments in `restore.rs` explicitly acknowledge partial/segmented restore modes with resumption via `db_next_version`), so it is not a purely theoretical edge case, but it also is not the common/default path (normal restores complete `get_usage` successfully). I could not fully verify from the available index whether `get_usage` can return `Err` under any realistic resumed-restore condition actually exercised in production restore flows (the implementation of `get_usage`/`StateStorageUsageSchema` lookup and how it's seeded during phase 1 wasn't available in the indexed excerpt), so likelihood is assessed as moderate rather than confirmed to be trivially reachable.

### Recommendation
- Make the `get_usage` failure explicit and fatal (or at least logged and tracked) rather than silently short-circuiting the whole state-indexing branch via `&&`.
- Only advance `LedgerCommitProgress`/`OverallCommitProgress` past a range if the state-indexing step for that range (when `kv_replay` is requested) has definitively succeeded, or split the progress marker so state-indexing progress is tracked independently from ledger-data progress, so a resumed restore can detect and correct the gap.

### Proof of Concept
1. Start a restore in phase 1 (`kv_replay=true`) for a version range where, due to a partial/interrupted prior restore attempt, `state_store.get_usage(Some(first_version - 1))` returns `Err` (e.g., the previous chunk's `VersionDataSchema`/usage entry wasn't yet written when this chunk starts).
2. `save_transactions_impl` runs: transactions/txn-infos/accumulator/events/write-sets for `[first_version, last_version]` are all written to the ledger DB batch as normal.
3. The `if kv_replay && first_version > 0 && state_store.get_usage(...).is_ok()` branch evaluates to `false`; `calculate_state_and_put_updates`/`set_state_ignoring_summary` never run for this chunk — no JMT nodes or state-KV entries are derived from `write_sets`.
4. `LedgerCommitProgress` and `OverallCommitProgress` are still set to `last_version`.
5. `RestoreCoordinator` reads `get_next_expected_transaction_version()` (driven by these progress markers) on any subsequent resume and considers this range fully restored, never revisiting it to backfill state indexing — the state tree for `[first_version, last_version]` permanently diverges from the transactions/write-sets recorded as committed.

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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L277-289)
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
```

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L96-103)
```rust
    /// The first phase is restore till the tree snapshot before the target version. It includes the following work
    /// a. restore the KV snapshot before ledger history start version, which also restore StateStorageUsage at the version
    /// b. start from the first transaction of loaded chunk, save the txn accumualator, and apply transactions till the KV snapshot. We don't restore state KVs here since we can't calculate StateStorageUsage before the KV snapshot.
    /// we start save transaction and restore KV after kv_snapshot version till the tree_snapshot before target version
    ///
    /// The second phase is restore the tree snapshot and replay txns till the target version
    /// a. restore the tree snapshot
    /// b. replay the txn till the target version
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
