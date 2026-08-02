Based on my investigation, I found one concrete, locally-provable gap: the plain transaction-restore path in `storage/backup/backup-cli` persists `write_sets` into `WriteSetDb` and (when `kv_replay` is enabled) uses them to reconstruct JMT state, **without ever checking that the write-set hash matches `TransactionInfo::state_change_hash()`**. I could not find the definition of `RestoreHandler::save_transactions` (the wrapper that backup-cli calls before reaching `restore_utils::save_transactions`) in the index, so I cannot be 100% certain no check happens there — this is a real gap in my verification. I'm reporting what I could concretely confirm, with that caveat stated explicitly.

### Title
Backup/replay path persists and replays unverified `WriteSet` data with no binding to `TransactionInfo::state_change_hash` - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, `storage/aptosdb/src/backup/restore_utils.rs`)

### Summary
`LoadedChunk::load` deserializes `(txn, aux_info, txn_info, events, write_set)` tuples straight from the backup file and only runs cryptographic verification (`txn_list_with_proof.verify(...)`) over a `TransactionListWithProofV2` that is built from `txns`, `event_vecs`, and `txn_infos` — the `write_sets` vector is **not** part of that verified structure. The unverified `write_sets` are later handed to `restore_utils::save_transactions_impl`, which writes them into `WriteSetDb` and, when `kv_replay` is true, feeds them into `state_store.calculate_state_and_put_updates` to rebuild the Jellyfish Merkle state — again with no check that `CryptoHash::hash(write_set) == txn_info.state_change_hash()`.

### Finding Description [1](#0-0) 
`LoadedChunk::load` reads each record's `write_set` from the on-disk chunk file and only passes `txn_list_with_proof` (transactions/events/txn_infos, not write_sets) through `verify()`. The `write_sets` vector is returned untouched, with no hash tied to the accumulator-proven `txn_infos`. [2](#0-1) 
`save_transactions_impl` writes `write_sets` into `WriteSetDb` unconditionally, and, if `kv_replay` is set, calls `state_store.calculate_state_and_put_updates` using those same write sets to derive `ledger_state`, which is then persisted with `set_state_ignoring_summary` — again no comparison against `txn_infos[idx].state_change_hash()`.

By contrast, the codebase does have this exact check elsewhere, confirming it's a recognized invariant that's simply missing here: [3](#0-2) [4](#0-3) 
Both `TransactionOutput::ensure_match_transaction_info` and `TransactionOutputListWithProof::verify` enforce `write_set_hash == txn_info.state_change_hash()`. The plain-transaction backup/restore path (used by `db-backup`/`db-restore` and referenced by `kv_replay`) has no equivalent gate before the write set is committed to durable storage or used to recompute state.

### Impact Explanation
If the `write_sets` blob in a transaction backup chunk is corrupted or tampered with (bit flip, truncated/replaced file on the backup storage, backup-storage compromise, etc.) while `txn_infos`/proof remain intact, `save_transactions_impl` will silently persist the wrong `WriteSet` under `WriteSetDb` for that version, and if `kv_replay` is on, will derive a JMT state root that diverges from the state actually committed on-chain (which is bound to `state_change_hash` in the proven `TransactionInfo`). This is exactly a "committed state that differs from the correct VM result" — subsequent `get_write_set_iterator`/replay-verify tooling and any downstream API/indexer that reads from this restored DB would serve corrupted, unauthenticated data as if it were validated ledger content.

### Likelihood Explanation
Moderate to low. The trigger requires an operator to restore from a corrupted or maliciously modified backup archive/manifest — this is not a fully "unprivileged" network attack path since backup files are typically supplied by the node operator or a trusted backup service, which weakens the severity relative to a pure on-chain/consensus vulnerability. However, backups are frequently pulled from remote/cloud storage that may not always be under the same trust boundary as the validator itself, and the missing check is a clear, local, reproducible logic gap (absence of a hash-binding assertion that exists in sibling code paths).

### Recommendation
In `LoadedChunk::load` (or in `restore_utils::save_transactions_impl`), assert `CryptoHash::hash(&write_set) == txn_info.state_change_hash()` for every `(write_set, txn_info)` pair before persisting to `WriteSetDb` and before using `write_sets` in `kv_replay`'s `calculate_state_and_put_updates`, mirroring the existing check in `TransactionOutput::ensure_match_transaction_info`.

### Proof of Concept
Not independently executable within this review — this requires constructing a tampered backup transaction-chunk file where `write_set` for a given record differs from the one whose hash produced `txn_info.state_change_hash()`, then running `db-restore`/`replay-verify` with `--kv-only-replay` (`kv_replay = true`) and observing that `save_transactions_impl` accepts and commits the mismatched write set without error, since no comparison against `state_change_hash` exists in that code path.

**Caveat on confidence**: I was unable to locate the definition of the `RestoreHandler::save_transactions` wrapper that bridges `backup-cli` to `storage/aptosdb/src/backup/restore_utils.rs` in the indexed code (it may be excluded due to index size limits). If that wrapper performs the missing hash check before calling into `restore_utils`, this finding would be moot. I recommend a Devin session with full repo access to confirm this wrapper's contents before treating this as fully verified.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L112-167)
```rust
        while let Some(record_bytes) = file.read_record_bytes().await? {
            let (txn, aux_info, txn_info, events, write_set): (
                _,
                PersistedAuxiliaryInfo,
                _,
                _,
                WriteSet,
            ) = match manifest.format {
                TransactionChunkFormat::V0 => {
                    let (txn, txn_info, events, write_set) = bcs::from_bytes(&record_bytes)?;
                    (
                        txn,
                        PersistedAuxiliaryInfo::None,
                        txn_info,
                        events,
                        write_set,
                    )
                },
                TransactionChunkFormat::V1 => bcs::from_bytes(&record_bytes)?,
            };
            txns.push(txn);
            persisted_aux_info.push(aux_info);
            txn_infos.push(txn_info);
            event_vecs.push(events);
            write_sets.push(write_set);
        }

        ensure!(
            manifest.first_version + (txns.len() as Version) == manifest.last_version + 1,
            "Number of items in chunks doesn't match that in manifest. first_version: {}, last_version: {}, items in chunk: {}",
            manifest.first_version,
            manifest.last_version,
            txns.len(),
        );

        let (range_proof, ledger_info) = storage
            .load_bcs_file::<(TransactionAccumulatorRangeProof, LedgerInfoWithSignatures)>(
                &manifest.proof,
            )
            .await?;
        if let Some(epoch_history) = epoch_history {
            epoch_history.verify_ledger_info(&ledger_info)?;
        }

        // make a `TransactionListWithProof` to reuse its verification code.
        let txn_list_with_proof =
            TransactionListWithProofV2::new(TransactionListWithAuxiliaryInfos::new(
                TransactionListWithProof::new(
                    txns,
                    Some(event_vecs),
                    Some(manifest.first_version),
                    TransactionInfoListWithProof::new(range_proof, txn_infos),
                ),
                persisted_aux_info,
            ));
        txn_list_with_proof.verify(ledger_info.ledger_info(), Some(manifest.first_version))?;
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L258-276)
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

```

**File:** types/src/transaction/mod.rs (L2168-2178)
```rust
        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(
            write_set_hash == txn_info.state_change_hash(),
            "{}: version:{}, write_set_hash:{:?}, expected:{:?}, write_set: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            write_set_hash,
            txn_info.state_change_hash(),
            self.write_set,
            expected_write_set,
        );
```

**File:** types/src/transaction/mod.rs (L2976-2984)
```rust
            // Verify the write set matches for both the transaction info and output
            let write_set_hash = CryptoHash::hash(&txn_output.write_set);
            ensure!(
                txn_info.state_change_hash() == write_set_hash,
                "The write set in transaction output does not match the transaction info \
                     in proof. Hash of write set in transaction output: {}. Write set hash in txn_info: {}.",
                write_set_hash,
                txn_info.state_change_hash(),
            );
```
