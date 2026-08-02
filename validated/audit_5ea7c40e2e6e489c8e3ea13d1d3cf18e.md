## Title
State-checkpoint version desync during backup restore due to dropped checkpoint indices - (File: `storage/aptosdb/src/backup/restore_utils.rs`)

## Summary
`save_transactions_impl`'s `kv_replay` branch always calls `StateUpdateRefs::index_write_sets` with an empty `all_checkpoint_indices` vector, even though the correct checkpoint boundaries are trivially derivable from the very `txn_infos` slice being committed in the same function call. This causes the state store's tracked `last_checkpoint` version/root to silently diverge from the real state-checkpoint version recorded in the committed, authoritative `TransactionInfo`s and transaction accumulator during backup/transaction restore.

## Finding Description
In `storage/aptosdb/src/backup/restore_utils.rs`: [1](#0-0) 

`save_transactions_impl` unconditionally passes `vec![]` for `all_checkpoint_indices` when replaying key-value data (`kv_replay == true`), regardless of whether any of the transactions in `[first_version, first_version+txns.len())` are actual state-checkpoint transactions. This same function already receives `txn_infos: &[TransactionInfo]` and, a few lines earlier, persists them verbatim via `TransactionInfoDb::put_transaction_info` and `put_transaction_accumulator`: [2](#0-1) 

Elsewhere in the same crate, the equivalent write-set-replay code path (`replay_write_sets_after_snapshot`, used for normal DB-restart JMT catch-up) computes checkpoint indices *correctly* from those same `TransactionInfo`s: [3](#0-2) 

`StateUpdateRefs::index` (the function backing `index_write_sets`) treats `all_checkpoint_indices` as authoritative: if it's empty, the entire chunk is folded into `for_latest` only, and `for_last_checkpoint` is `None`: [4](#0-3) 

This flag is then load-bearing in `State::update_with_memorized_reads` (invoked transitively from `calculate_state_and_put_updates`): when `for_last_checkpoint_batched()` is `None`, the new `last_checkpoint` is **not recomputed** — it is just cloned from the old value, even if the chunk actually crosses a real checkpoint boundary: [5](#0-4) 

The resulting `LedgerState` (with a stale/incorrect `last_checkpoint`) is then persisted as the DB's tracked state via `state_store.set_state_ignoring_summary(ledger_state)`: [1](#0-0) 

## Impact Explanation
After a `kv_replay` restore of a chunk that contains one or more real state-checkpoint transactions, the DB's in-memory/persisted `LedgerState.last_checkpoint` no longer matches the checkpoint version/root actually reflected in the committed `TransactionInfo`s and transaction accumulator for that range. Because `last_checkpoint` is what downstream consumers use to determine the "authoritative" state-snapshot version — e.g. `get_latest_state_checkpoint_version`, state-Merkle-DB snapshot lookups, and later JMT catch-up/replay logic that restarts from `last_checkpoint`'s next version — this desync causes the state-Merkle/JMT tree to never actually be materialized/persisted at the true checkpoint version during the replay, while the ledger metadata and accumulator claim that checkpoint hash is valid. Any subsequent state-value-with-proof or state-snapshot query anchored on that version can therefore be served against the wrong Merkle root/version, or a state-snapshot never gets built for a version that the committed `TransactionInfo.state_checkpoint_hash` claims exists — a durable, unprivileged proof/commitment-binding break introduced purely by local restore code, not by any adversarial or privileged actor.

## Likelihood Explanation
This is deterministic, not exploit-dependent: any transaction restore via the backup-cli path that invokes `kv_replay=true` (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs` via `restore_handler.rs` / `storage/backup/backup-cli/src/coordinators/restore.rs`) over a chunk spanning at least one real checkpoint transaction will hit this code path. No special privileges, malicious peers, or races are required — it is a straightforward operator/self-triggered correctness bug in the restore tool that ships with aptos-core.

## Recommendation
In `save_transactions_impl`, compute `all_checkpoint_indices` from `txn_infos` (mirroring `replay_write_sets_after_snapshot`'s `positions(|txn_info| txn_info.has_state_checkpoint_hash())`) instead of passing `vec![]`, so `StateUpdateRefs::index_write_sets` correctly splits the chunk into `for_last_checkpoint`/`for_latest` and the persisted `last_checkpoint` state stays consistent with the committed `TransactionInfo`/accumulator data.

## Proof of Concept
1. Restore a transaction chunk `[v0, v3]` via `restore_utils::save_transactions(..., kv_replay=true)` where, say, `txn_infos[1]` is a real state-checkpoint (`has_state_checkpoint_hash() == true`) but `txn_infos[3]` is not the last one either.
2. `save_transactions_impl` calls `StateUpdateRefs::index_write_sets(first_version, write_sets, 4, vec![])` — note the hardcoded empty vec, ignoring that `txn_infos[1]` is a checkpoint.
3. `StateUpdateRefs::index` therefore builds only a `for_latest` bucket covering all 4 versions; `for_last_checkpoint` is `None`.
4. `calculate_state_and_put_updates` → `update_with_memorized_reads` leaves `last_checkpoint` as the pre-chunk value instead of the state at version 1.
5. `state_store.set_state_ignoring_summary(ledger_state)` persists this stale `last_checkpoint`, while `TransactionInfoDb`/`transaction_accumulator_db` for version 1 still assert a valid `state_checkpoint_hash`, producing a durable mismatch between the ledger's tracked checkpoint state and the committed proof data for that version.

### Citations

**File:** storage/aptosdb/src/backup/restore_utils.rs (L223-237)
```rust
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

**File:** storage/aptosdb/src/state_store/mod.rs (L932-948)
```rust
        let txn_info_iter = state_db
            .ledger_db
            .transaction_info_db()
            .get_transaction_info_iter(snapshot_next_version, write_sets.len())?;
        let all_checkpoint_indices = txn_info_iter
            .into_iter()
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .positions(|txn_info| txn_info.has_state_checkpoint_hash())
            .collect();

        let state_update_refs = StateUpdateRefs::index_write_sets(
            snapshot_next_version,
            &write_sets,
            write_sets.len(),
            all_checkpoint_indices,
        );
```

**File:** storage/storage-interface/src/state_store/state_update_refs.rs (L190-219)
```rust
        let mut updates_by_version = updates_by_version.into_iter();
        let mut num_versions_for_last_checkpoint = 0;
        let last_checkpoint_index = all_checkpoint_indices.last().copied();

        let for_last_checkpoint = last_checkpoint_index.map(|index| {
            num_versions_for_last_checkpoint = index + 1;
            let per_version = PerVersionStateUpdateRefs::index(
                first_version,
                updates_by_version
                    .by_ref()
                    .take(num_versions_for_last_checkpoint),
                num_versions_for_last_checkpoint,
            );
            let batched = Self::batch_updates(&per_version);
            (per_version, batched)
        });

        let for_latest = match last_checkpoint_index {
            Some(index) if index + 1 == num_versions => None,
            _ => {
                assert!(num_versions_for_last_checkpoint < num_versions);
                let per_version = PerVersionStateUpdateRefs::index(
                    first_version + num_versions_for_last_checkpoint as Version,
                    updates_by_version,
                    num_versions - num_versions_for_last_checkpoint,
                );
                let batched = Self::batch_updates(&per_version);
                Some((per_version, batched))
            },
        };
```

**File:** storage/storage-interface/src/state_store/state.rs (L495-511)
```rust
        let last_checkpoint = if let Some(batched) = updates.for_last_checkpoint_batched() {
            let per_version = updates
                .for_last_checkpoint_per_version()
                .expect("Both per-version and batched updates should exist.");
            let (new_ckpt, hot_state_updates) = self.latest().update(
                Arc::clone(&persisted_hot_view),
                persisted_snapshot,
                batched,
                per_version,
                updates.all_checkpoint_versions(),
                reads,
            )?;
            all_hot_state_updates.for_last_checkpoint = Some(hot_state_updates);
            new_ckpt
        } else {
            self.last_checkpoint.clone()
        };
```
