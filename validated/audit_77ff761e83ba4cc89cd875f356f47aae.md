## Title
Backup restore path saves and replays unverified `write_set` bytes not authenticated against the accumulator-proven `TransactionInfo.state_change_hash` - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
The `go_through_verified_chunks`/restore path (used when `replay_from_version` is not set, i.e. the default DB-restore-without-replay flow) loads `write_set` bytes straight out of the backup file and persists them to `WriteSetDb`, and—when `kv_replay` is enabled—feeds them into `state_store.calculate_state_and_put_updates` to materialize live state-KV values, without ever checking that the loaded `write_set`'s hash equals the `state_change_hash` inside the corresponding, accumulator-authenticated `TransactionInfo`.

### Finding Description
`LoadedChunk::load` (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs:99-186`) deserializes `(txn, aux_info, txn_info, events, write_set)` tuples from the backup file and keeps `write_sets` in a side vector, separate from the `TransactionListWithProofV2` object it builds for verification: [1](#0-0) 

The `.verify()` call used here is `TransactionListWithProof::verify`, which only checks transaction hashes, event root hashes, and the accumulator proof of `txn_infos` against the `LedgerInfo`: [2](#0-1) 

Unlike `TransactionOutputListWithProof::verify`, which explicitly checks `txn_info.state_change_hash() == CryptoHash::hash(&txn_output.write_set)` before trusting a write set: [3](#0-2) 

`write_sets` never passes through that check on the `save_transactions`/`replay_kv` code path. `restore_utils::save_transactions_impl` writes the (unverified) `write_sets` directly into `WriteSetDb`, and, when `kv_replay` is true, also uses them to compute and commit the live state KV values via `state_store.calculate_state_and_put_updates`: [4](#0-3) 

`RestoreHandler::save_transactions` and `save_transactions_and_replay_kv` both forward attacker/storage-controlled `write_sets` unchanged into this function: [5](#0-4) 

By contrast, the *tree-restore*/*replay* path (`replay_from_version` set) routes through `ChunkExecutor`/`TransactionReplayer::enqueue_chunks`, which re-executes/re-applies transactions and, via `TransactionOutput::ensure_match_transaction_info`, checks `write_set_hash == txn_info.state_change_hash()` before commit: [6](#0-5) 

But when `db_next_version < tree_snapshot.version` and a `kv_snapshot` is used (`coordinators/restore.rs` "phase 1"), or more generally whenever `TransactionRestoreBatchController::save_before_replay_version` is used to persist transactions "before replay version" (not through the executor), the raw `write_sets` loaded in `LoadedChunk` are saved via `RestoreHandler::save_transactions`/`save_transactions_and_replay_kv`, bypassing `ensure_match_transaction_info`: [7](#0-6) 

Since `TransactionInfo` is what the transaction accumulator / `LedgerInfoWithSignatures` actually authenticates, an attacker who controls or corrupts the backup blob storage (the write-set records in the manifest file) can substitute an arbitrary write set for any historical transaction while keeping the original, validator-signed `TransactionInfo` intact. The restore code accepts this combination as valid because it never re-derives or checks `state_change_hash` for the write-set bytes on this path.

### Impact Explanation
This breaks the write-set/`TransactionInfo` invariant that this codebase enforces everywhere else (VM output verification, `TransactionOutputListWithProof::verify`, `ensure_match_transaction_info`). A tampered/corrupted backup can cause a restored (and, with `kv_replay`, KV-replayed) full node or archive node to persist and serve state values that were never actually produced by VM execution nor validator-signed, while `WriteSetDb`, transaction lookups, and (if `kv_replay` is used) live account state diverge from the real historical ledger state — i.e. committed durable state differing from the correct VM result, served via authenticated-looking APIs (`get_write_set_iterator`, `get_state_value_by_version`, etc.) that give no indication of the discrepancy since the transaction/event/accumulator proofs are otherwise still valid.

### Likelihood Explanation
This requires control over the backup source (compromised/malicious backup storage backend, or a MITM'd/tampered archive/objects store) rather than a fully permissionless network attacker, so it is not a wide-open exploit path. It also only manifests on the specific restore/`kv_replay` sub-path that bypasses `ChunkExecutor` re-execution (`save_before_replay_version`/`save_transactions_and_replay_kv`), not the full re-execution restore path which does check `ensure_match_transaction_info`. It is nonetheless a genuine unprivileged (from the node operator's perspective, trusting only the backup pipeline) integrity gap given the code comment itself even flags a related concern ("comparator ignores the checkpoint hashes ... report a successful replay even when the authenticated ... root diverges from local execution").

### Recommendation
On every path that loads `write_sets` from backup files and persists/replays them without full VM re-execution (`LoadedChunk::load`, `save_before_replay_version`, `RestoreHandler::save_transactions`/`save_transactions_and_replay_kv`), verify `CryptoHash::hash(&write_set) == txn_info.state_change_hash()` for each transaction before writing to `WriteSetDb` or feeding into `calculate_state_and_put_updates`, mirroring the check already done in `TransactionOutputListWithProof::verify` / `TransactionOutput::ensure_match_transaction_info`.

### Proof of Concept
1. Take a legitimate transaction backup manifest/chunk file containing `(txn, aux_info, txn_info, events, write_set)` records, where `txn_info` is part of a chunk whose accumulator proof and `LedgerInfoWithSignatures` are valid.
2. Replace one record's `write_set` bytes with an attacker-chosen `WriteSet` (leaving `txn`, `txn_info`, and `events` untouched), so `CryptoHash::hash(&write_set) != txn_info.state_change_hash()`.
3. Run `TransactionRestoreController`/`TransactionRestoreBatchController` in restore mode without `replay_from_version` (or with `replay_from_version` pointed at KV-only replay per `coordinators/restore.rs` phase 1). `LoadedChunk::load` calls `txn_list_with_proof.verify(...)`, which succeeds because it never checks write-set content.
4. `save_before_replay_version` → `RestoreHandler::save_transactions`/`save_transactions_and_replay_kv` → `restore_utils::save_transactions_impl` persists the tampered `write_set` into `WriteSetDb` and (if `kv_replay`) commits the corresponding state values, all without any error.
5. Querying the restored node's `WriteSetDb` or state via `get_write_set_iterator`/`get_state_value_by_version` for that version returns the attacker-substituted data, even though the transaction, events, and accumulator proof still verify successfully against the original signed `LedgerInfo`.

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

**File:** types/src/transaction/mod.rs (L2693-2731)
```rust
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_version: Option<Version>,
    ) -> Result<()> {
        // Verify the first transaction versions match
        ensure!(
            self.get_first_transaction_version() == first_transaction_version,
            "First transaction version ({:?}) doesn't match given version ({:?}).",
            self.get_first_transaction_version(),
            first_transaction_version,
        );

        // Verify the lengths of the transactions and transaction infos match
        ensure!(
            self.proof.transaction_infos.len() == self.get_num_transactions(),
            "The number of TransactionInfo objects ({}) does not match the number of \
             transactions ({}).",
            self.proof.transaction_infos.len(),
            self.get_num_transactions(),
        );

        // Verify the transaction hashes match those of the transaction infos
        self.transactions
            .par_iter()
            .zip_eq(self.proof.transaction_infos.par_iter())
            .map(|(txn, txn_info)| {
                let txn_hash = txn.committed_hash();
                ensure!(
                    txn_hash == txn_info.transaction_hash(),
                    "The hash of transaction does not match the transaction info in proof. \
                     Transaction hash: {:x}. Transaction hash in txn_info: {:x}.",
                    txn_hash,
                    txn_info.transaction_hash(),
                );
                Ok(())
            })
            .collect::<Result<Vec<_>>>()?;

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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L258-275)
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

**File:** storage/aptosdb/src/backup/restore_handler.rs (L78-126)
```rust
    pub fn save_transactions(
        &self,
        first_version: Version,
        txns: &[Transaction],
        persisted_aux_info: &[PersistedAuxiliaryInfo],
        txn_infos: &[TransactionInfo],
        events: &[Vec<ContractEvent>],
        write_sets: Vec<WriteSet>,
    ) -> Result<()> {
        restore_utils::save_transactions(
            self.state_store.clone(),
            self.ledger_db.clone(),
            first_version,
            txns,
            persisted_aux_info,
            txn_infos,
            events,
            write_sets,
            None,
            false,
        )
    }

    pub fn force_state_version_for_kv_restore(&self, version: Option<Version>) -> Result<()> {
        self.state_store.init_state_ignoring_summary(version)
    }

    pub fn save_transactions_and_replay_kv(
        &self,
        first_version: Version,
        txns: &[Transaction],
        persisted_aux_info: &[PersistedAuxiliaryInfo],
        txn_infos: &[TransactionInfo],
        events: &[Vec<ContractEvent>],
        write_sets: Vec<WriteSet>,
    ) -> Result<()> {
        restore_utils::save_transactions(
            self.state_store.clone(),
            self.ledger_db.clone(),
            first_version,
            txns,
            persisted_aux_info,
            txn_infos,
            events,
            write_sets,
            None,
            true,
        )
    }
```

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L242-300)
```rust
            // phase 1.a: restore the kv snapshot
            if kv_snapshot.is_some() {
                let kv_snapshot = kv_snapshot.clone().unwrap();
                info!("Start restoring KV snapshot at {}", kv_snapshot.version);

                StateSnapshotRestoreController::new(
                    StateSnapshotRestoreOpt {
                        manifest_handle: kv_snapshot.manifest,
                        version: kv_snapshot.version,
                        validate_modules: false,
                        restore_mode: StateSnapshotRestoreMode::KvOnly,
                    },
                    self.global_opt.clone(),
                    Arc::clone(&self.storage),
                    epoch_history.clone(),
                )
                .run()
                .await?;
            }

            // phase 1.b: save the txn between the first txn of the first chunk and the tree snapshot
            let txn_manifests = transaction_backups
                .iter()
                .filter(|e| {
                    e.first_version <= tree_snapshot.version && e.last_version >= db_next_version
                })
                .map(|e| e.manifest.clone())
                .collect();
            assert!(
                db_next_version == 0
                    || transaction_backups.first().map_or(0, |t| t.first_version)
                        <= db_next_version,
                "Inconsistent state: first txn version {} is larger than db_next_version {}",
                transaction_backups.first().map_or(0, |t| t.first_version),
                db_next_version
            );
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
