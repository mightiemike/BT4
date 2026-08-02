### Title
Silent skip of KV/state update during backup restore lets `OverallCommitProgress`/`LedgerCommitProgress` advance past unindexed state, corrupting restored ledger state and authenticated state reads - (File: storage/aptosdb/src/backup/restore_utils.rs)

### Summary
`save_transactions_impl` gates the state-KV materialization step (`calculate_state_and_put_updates` / `set_state_ignoring_summary`) behind `kv_replay && first_version > 0 && state_store.get_usage(Some(first_version - 1)).is_ok()`. If the usage lookup for `first_version - 1` fails (e.g. because that version's storage usage isn't yet indexed during a partial/staged restore), the block is silently skipped — no error is propagated — yet the function unconditionally advances `LedgerCommitProgress` and `OverallCommitProgress` to `last_version` right after. [1](#0-0) 

### Finding Description
`save_transactions_impl` writes transactions, transaction infos, the accumulator, events, and write sets unconditionally, but the actual state-key-value materialization (which computes and persists the new `LedgerState`/state values for the committed versions) only happens inside the `if kv_replay && first_version > 0 && state_store.get_usage(...).is_ok()` guard. [2](#0-1) 

Regardless of whether that branch executes, the function then writes `DbMetadataKey::LedgerCommitProgress` and `DbMetadataKey::OverallCommitProgress` to `last_version`. [3](#0-2) 

These two progress markers are the canonical source of truth for "what version has been fully, consistently committed" elsewhere in the DB: `get_synced_version()` reads `OverallCommitProgress` and `get_ledger_commit_progress()` reads `LedgerCommitProgress`, and both are treated by startup/consistency logic and read-serving code as proof that all data (including state) up to that version is present and correct. [4](#0-3) 

Because the `if` condition depends on a data-availability check (`get_usage(...).is_ok()`) rather than on whether the KV materialization actually needs to run, any transient or ordering failure in that lookup (which is plausible in restore/replay flows that stage phases and versions out of the usual monotonic order, as seen in the multi-phase `coordinators/restore.rs` restore driver) causes the function to record versions as fully committed while their state values were never written to the state-KV DB and the in-memory `LedgerState` was never advanced via `set_state_ignoring_summary`. This is the same bug class as the SiloAMO report: a fallible check silently gates a critical step, but the surrounding "finalize" logic proceeds unconditionally as if the step succeeded, leaving durable committed markers inconsistent with actual data.

### Impact Explanation
If `OverallCommitProgress`/`LedgerCommitProgress` are advanced without the corresponding state values being committed, any subsequent read through the state-KV DB for those versions (state proofs, `db_state_view`, API responses bound to a specific version) will be served against stale/missing data while the database metadata asserts the version is committed and consistent. This corrupts durable ledger data — the state-store contents diverge from what the committed progress markers claim, which is exactly the "committed state differs from correct result" and "authenticated API/state-view output bound to wrong version" impact categories in scope.

### Likelihood Explanation
This code path is restore/backup-specific (`kv_replay` is only true for the `save_transactions_and_replay_kv` KV-only replay entry point) rather than normal-operation consensus commit, so it does not require a malicious actor — it can be triggered by ordinary restore-flow conditions where `get_usage(first_version - 1)` is not yet available (e.g., partial restores, mismatched phase ordering, or an as-yet-un-computed usage record). I was not able to fully verify from the available index every caller sequencing guarantee that `get_usage(first_version - 1)` always succeeds by the time `kv_replay=true` restore runs (the multi-phase restore coordinator in `storage/backup/backup-cli/src/coordinators/restore.rs` stages KV-only and tree-only restores in an order that could plausibly hit this), so likelihood is assessed as plausible but not confirmed to be reliably reachable on a specific version sequence without deeper tracing of `get_usage`'s failure modes.

### Recommendation
Do not use `.is_ok()` as a silent skip condition for state materialization. Either:
1. Propagate the error from `get_usage` (`?`) instead of swallowing it via `.is_ok()`, so failures abort the restore instead of silently omitting state updates, or
2. Make the commit-progress advancement conditional on the state materialization actually having succeeded, so `LedgerCommitProgress`/`OverallCommitProgress` never claims a version is committed unless state was actually indexed for it.

### Proof of Concept
Not independently confirmed with a concrete runnable repro from the indexed code alone — the exact trigger requires demonstrating a real caller path where `state_store.get_usage(Some(first_version - 1))` returns `Err` while `kv_replay` is `true` (e.g., via `RestoreHandler::save_transactions_and_replay_kv`) during a legitimate restore sequence. Given the tool-call budget is exhausted, I could not trace `get_usage`'s implementation and all its failure conditions to conclusively prove reachability; this should be verified with a targeted restore-flow test (drive `save_transactions_and_replay_kv` with `first_version` such that usage for `first_version-1` is absent, then confirm `OverallCommitProgress` advances while state-KV entries for the new versions are absent).

### Citations

**File:** storage/aptosdb/src/backup/restore_utils.rs (L258-289)
```rust
    // insert changes in write set schema batch
    for (idx, ws) in write_sets.iter().enumerate() {
        WriteSetDb::put_write_set(
            first_version + idx as Version,
            ws,
            &mut ledger_db_batch.write_set_db_batches,
        )?;
    }

    if kv_replay && first_version > 0 && state_store.get_usage(Some(first_version - 1)).is_ok() {
        let (ledger_state, _hot_state_updates) = state_store.calculate_state_and_put_updates(
            &StateUpdateRefs::index_write_sets(first_version, write_sets, write_sets.len(), vec![]),
            &mut ledger_db_batch.ledger_metadata_db_batches, // used for storing the storage usage
            state_kv_batches,
        )?;
        // n.b. ideally this is set after the batches are committed
        state_store.set_state_ignoring_summary(ledger_state);
    }

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

**File:** storage/aptosdb/src/ledger_db/ledger_metadata_db.rs (L76-83)
```rust
    pub(crate) fn get_synced_version(&self) -> Result<Option<Version>> {
        get_progress(&self.db, &DbMetadataKey::OverallCommitProgress)
    }

    pub(crate) fn get_ledger_commit_progress(&self) -> Result<Version> {
        get_progress(&self.db, &DbMetadataKey::LedgerCommitProgress)?
            .ok_or_else(|| AptosDbError::NotFound("No LedgerCommitProgress in db.".to_string()))
    }
```
