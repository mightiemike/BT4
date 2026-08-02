## Finding: Backup write-set chunks are committed to storage without verification against the transaction-info's `state_change_hash`

### Summary
The transaction backup/restore path in `storage/backup/backup-cli/src/backup_types/transaction/restore.rs` reads `write_sets` from a backup chunk file and separately verifies only the transactions, events, and the transaction-info accumulator proof — never the write sets — before handing them to `restore_utils::save_transactions_impl`, which writes them straight to the `WriteSetDb` via `WriteSetDb::put_write_set`. This is the same low-level `put_write_set` used by the normal execution-commit path (`commit_write_sets`), but the restore path reaches it without the write-set-integrity check that the equivalent state-sync verification path (`TransactionOutputListWithProof::verify`) performs.

### Finding Description
In `LoadedChunk::load` [1](#0-0) , each record in the backup chunk is deserialized into `(txn, aux_info, txn_info, events, write_set)`, and `write_sets` is populated into its own vector, separate from the fields that get wrapped into `TransactionListWithProof`: [2](#0-1) 

Only `txns`, `event_vecs`, and `txn_infos` are put into the `TransactionListWithProofV2`, and `txn_list_with_proof.verify(...)` is called to validate against the ledger info. That verification, `TransactionListWithProof::verify`, checks:
- transaction hash vs `txn_info.transaction_hash()`
- the accumulator proof of `txn_infos` against the ledger info's `transaction_accumulator_hash()`
- events vs the event root hash in `txn_info` [3](#0-2) 

It never checks the write set against `txn_info.state_change_hash()`. Contrast this with `TransactionOutputListWithProof::verify` (used by other consumers of write sets/outputs, e.g. state-sync output verification), which explicitly performs this check: [4](#0-3) 

After `LoadedChunk::load` completes, the unverified `write_sets` vector (never re-associated with any hash check) flows into `save_before_replay_version`, which calls `restore_handler.save_transactions(first_version, &txns_to_save, ..., write_sets_to_save)`: [5](#0-4) 

This reaches `restore_utils::save_transactions_impl` in aptosdb, which writes each `WriteSet` directly: [6](#0-5) 

`WriteSetDb::put_write_set` used here is the exact same primitive used by `commit_write_sets` for normal transaction execution commits: [7](#0-6) 

So a backup chunk (which per the review scope can be externally-served/attacker-influenced) can carry a `write_set` whose BCS-serialized hash does not match `txn_info.state_change_hash()`, while the transaction, events, and accumulator proof for that same chunk still validate successfully — because the accumulator/transaction-info check never touches the write set content at all.

### Impact Explanation
A restoring node ingesting such a chunk would durably persist a `WriteSet` for a version that is inconsistent with the authoritative `TransactionInfo.state_change_hash` committed by consensus/accumulator. This corrupts `WriteSetDb` for the restored version: reads of `get_write_set`/`get_write_sets` for that version, and any KV-replay logic that indexes state updates from `write_sets` (`state_store.calculate_state_and_put_updates` in `save_transactions_impl`, gated by `kv_replay`), would derive state from a forged write set rather than the one actually validated by the accumulator. This is a hard-fork/state-divergence-class issue for restored nodes relative to the authoritative execution state, scoped to nodes performing this restore.

### Likelihood Explanation
The write-set field is fully attacker-controlled data within a backup chunk file (the "manifest.transactions" file), and nothing in `LoadedChunk::load` or downstream restore code cross-checks it against `state_change_hash` before commit. Any restore operation consuming a backup produced or served by an unprivileged/compromised party (matching the scope's assumption that backup chunks can be externally-served) will silently accept a mismatched write set as long as the transaction/events/proof portion is internally consistent.

### Recommendation
In `LoadedChunk::load` (or immediately before/alongside `save_before_replay_version`'s call into `restore_handler.save_transactions`), verify each `write_set` against its corresponding `txn_info.state_change_hash()` (i.e., `CryptoHash::hash(&write_set) == txn_info.state_change_hash()`), analogous to what `TransactionOutputListWithProof::verify` already does. Reject the chunk (return an `Err`) if any mismatch is found before write sets are ever passed to `restore_utils::save_transactions`/`WriteSetDb::put_write_set`.

### Proof of Concept
1. Produce a valid transaction backup manifest and chunk for a version range using the normal backup tooling.
2. In the chunk's record stream (BCS-encoded `(txn, aux_info, txn_info, events, write_set)` tuples), replace one `write_set` with an arbitrary different `WriteSet` (leaving `txn`, `txn_info`, and `events` untouched, so their hashes still match `txn_info`).
3. Run `TransactionRestoreController`/`TransactionRestoreBatchController::run_impl` against this manifest.
4. Observe that `LoadedChunk::load`'s `txn_list_with_proof.verify(...)` call succeeds (since it never inspects `write_sets`), and the tampered write set is subsequently persisted via `restore_utils::save_transactions` → `WriteSetDb::put_write_set` without any error.
5. Query `get_write_set` for that version afterward and confirm it returns the tampered write set rather than the one whose hash matches the committed `TransactionInfo.state_change_hash`.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L112-137)
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
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L156-185)
```rust
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
        // and disassemble it to get things back.
        let (txn_list_with_proof, persisted_aux_info) = txn_list_with_proof.into_parts();
        let txns = txn_list_with_proof.transactions;
        let range_proof = txn_list_with_proof
            .proof
            .ledger_info_to_transaction_infos_proof;
        let txn_infos = txn_list_with_proof.proof.transaction_infos;
        let event_vecs = txn_list_with_proof.events.expect("unknown to be Some.");

        Ok(Self {
            manifest,
            txns,
            persisted_aux_info,
            txn_infos,
            event_vecs,
            range_proof,
            write_sets,
        })
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L497-517)
```rust
                    // identify txns to be saved before the first_to_replay version
                    if first_version < first_to_replay {
                        let num_to_save =
                            (min(first_to_replay, last_version + 1) - first_version) as usize;
                        let txns_to_save: Vec<_> = txns.drain(..num_to_save).collect();
                        let persisted_aux_info_to_save: Vec<_> =
                            persisted_aux_info.drain(..num_to_save).collect();
                        let txn_infos_to_save: Vec<_> = txn_infos.drain(..num_to_save).collect();
                        let event_vecs_to_save: Vec<_> = event_vecs.drain(..num_to_save).collect();
                        let write_sets_to_save = write_sets.drain(..num_to_save).collect();
                        tokio::task::spawn_blocking(move || {
                            restore_handler.save_transactions(
                                first_version,
                                &txns_to_save,
                                &persisted_aux_info_to_save,
                                &txn_infos_to_save,
                                &event_vecs_to_save,
                                write_sets_to_save,
                            )
                        })
                        .await??;
```

**File:** types/src/transaction/mod.rs (L2693-2752)
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

        // Verify the transaction infos are proven by the ledger info.
        self.proof
            .verify(ledger_info, self.get_first_transaction_version())?;

        // Verify the events if they exist.
        if let Some(event_lists) = &self.events {
            ensure!(
                event_lists.len() == self.get_num_transactions(),
                "The length of event_lists ({}) does not match the number of transactions ({}).",
                event_lists.len(),
                self.get_num_transactions(),
            );
            event_lists
                .into_par_iter()
                .zip_eq(self.proof.transaction_infos.par_iter())
                .map(|(events, txn_info)| verify_events_against_root_hash(events, txn_info))
                .collect::<Result<Vec<_>>>()?;
        }

        Ok(())
    }
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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L258-265)
```rust
    // insert changes in write set schema batch
    for (idx, ws) in write_sets.iter().enumerate() {
        WriteSetDb::put_write_set(
            first_version + idx as Version,
            ws,
            &mut ledger_db_batch.write_set_db_batches,
        )?;
    }
```

**File:** storage/aptosdb/src/ledger_db/write_set_db.rs (L122-165)
```rust
    /// Commits write sets starting from `first_version` to the database.
    pub(crate) fn commit_write_sets(
        &self,
        first_version: Version,
        transaction_outputs: &[TransactionOutput],
    ) -> Result<()> {
        let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_write_sets"]);

        let chunk_size = transaction_outputs.len() / 4 + 1;
        let batches = transaction_outputs
            .par_chunks(chunk_size)
            .enumerate()
            .map(|(chunk_idx, chunk)| {
                let mut batch = self.db().new_native_batch();
                let chunk_first_version = first_version + (chunk_idx * chunk_size) as Version;

                chunk.iter().enumerate().try_for_each(|(i, txn_out)| {
                    Self::put_write_set(
                        chunk_first_version + i as Version,
                        txn_out.write_set(),
                        &mut batch,
                    )
                })?;
                Ok(batch)
            })
            .collect::<Result<Vec<_>>>()?;

        {
            let _timer = OTHER_TIMERS_SECONDS.timer_with(&["commit_write_sets___commit"]);
            for batch in batches {
                self.db().write_schemas(batch)?
            }
            Ok(())
        }
    }

    /// Saves executed transaction vm output given the `version`.
    pub(crate) fn put_write_set(
        version: Version,
        write_set: &WriteSet,
        batch: &mut impl WriteBatch,
    ) -> Result<()> {
        batch.put::<WriteSetSchema>(&version, write_set)
    }
```
