### Title
Backup/restore path commits `write_set` bytes to durable storage without binding them to the accumulator-verified `TransactionInfo.state_change_hash` - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, `storage/aptosdb/src/backup/restore_utils.rs`)

### Summary
The transaction-backup restore flow verifies the transactions, events, and `TransactionInfo` list against the target `LedgerInfoWithSignatures` via the transaction accumulator, but the `WriteSet` for each transaction is *not* included in that cryptographic verification. It is decoded from the same backup record and then persisted straight into `WriteSetDb` (and, in `kv_replay` mode, applied to the Jellyfish Merkle Tree) purely on the strength of positional pairing with an already-verified `TransactionInfo`, with no check that the write set's hash matches `TransactionInfo::state_change_hash`.

### Finding Description
In `LoadedChunk::load` [1](#0-0) , each backup record is deserialized into `(txn, aux_info, txn_info, events, write_set)`, and `write_sets` is kept as a plain side vector.

Verification is then performed by constructing a `TransactionListWithProofV2` from `txns`, `event_vecs`, and `txn_infos` and calling `.verify(...)` against the target ledger info [2](#0-1) . That verification hashes each `TransactionInfo` and checks the accumulator range proof against the ledger info's `transaction_accumulator_hash` (see `TransactionInfoListWithProof::verify`, which hashes `transaction_infos` and calls `ledger_info_to_transaction_infos_proof.verify(...)`) [3](#0-2) . This proves the `TransactionInfo` values (including their `state_change_hash` field, which is a hash *commitment* to the write set) are the ones actually signed into the ledger info. It does **not** verify that the sibling `write_set` value, loaded from the same untrusted backup blob, actually hashes to that committed `state_change_hash`.

`write_sets` is carried forward unchanged through `save_before_replay_version` and passed to `RestoreHandler::save_transactions` [4](#0-3) , which calls into `save_transactions_impl` in `aptosdb`. That function writes the `TransactionInfo` list into `TransactionInfoDb`/`TransactionAccumulatorSchema` (crypto-bound to the ledger info) and separately writes the raw `write_sets` into `WriteSetDb`, and — when `kv_replay` is set — feeds those same write sets into `state_store.calculate_state_and_put_updates` to materialize the actual JMT state updates [5](#0-4) . At no point in `save_transactions_impl` is `WriteSet`'s hash recomputed and compared to `txn_infos[idx].state_change_hash()`.

So the only invariant that is supposed to bind "this write set is what actually happened for this transaction" to the accumulator-anchored ledger state is `TransactionInfo.state_change_hash`, and this restore path never checks it.

### Impact Explanation
If the backup blob delivered by `BackupStorage` for the "save before replay" range is altered (or corrupted) so that the `write_set` bytes for a given record no longer match the `state_change_hash` embedded in the (still correctly accumulator-verified) `TransactionInfo`, the restore/state-sync-via-backup path will:
- persist the wrong `WriteSet` into `WriteSetDb` permanently, and
- (in `kv_replay` mode) apply the wrong state updates into the versioned JMT/state store,

while the `TransactionInfo`, transaction hash, and event root continue to check out against the signed ledger info. This produces committed durable state that diverges from the ledger info's cryptographic commitments and from the true VM result for that version — i.e., a wrong ledger state that appears to satisfy accumulator/proof checks, meeting the "committed state differs from the correct VM result / corrupts durable ledger data" impact bar for a restored/synced node.

### Likelihood Explanation
This is a genuine code gap in the local verification logic itself (a missing `state_change_hash` check), independent of any theory about a malicious backup operator: the restore code documents no equivalent check anywhere in the path I traced (`restore.rs`, `restore_utils.rs`), so any bit-level corruption or substitution of the `write_set` segment of a backup chunk — accidental or intentional — would silently pass all present verification. That said, exploitability in practice hinges on the trust model of the backup storage source; if backup storage is always operated and fully trusted by the node operator (a common deployment assumption), this reduces to a defense-in-depth gap rather than an attacker-reachable path, and I was not able to fully confirm within the available searches whether some other layer (e.g. VM-execution replay verification in `verify_execution_mode`) independently catches this for the replayed range (only the pre-replay "save" range is affected, as shown above).

### Recommendation
Before persisting any `write_set` in `save_transactions_impl` (or earlier, right after `LoadedChunk::load`), recompute the write set's commitment and assert it equals `txn_infos[idx].state_change_hash()`, mirroring the existing hash checks already performed for the transaction and event lists. This closes the gap regardless of how trusted the backup source is assumed to be.

### Proof of Concept
1. Take a valid transaction backup manifest/chunk.
2. In the transaction chunk file, replace one record's `write_set` field with a different, well-formed `WriteSet` (its `TransactionInfo`, transaction, and events are left untouched).
3. Run `TransactionRestoreController`/`aptos-db-tool` restore (or state-sync via backup) using `replay_from_version` set beyond this chunk's range so it goes through `save_transactions` directly rather than VM replay.
4. Observe that `LoadedChunk::load`'s call to `txn_list_with_proof.verify(...)` succeeds (it only checks txns/events/txn_infos against the ledger info) [6](#0-5) , and the tampered `write_set` is written into `WriteSetDb`/state store by `save_transactions_impl` without any error [5](#0-4) .

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L156-176)
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

**File:** types/src/proof/definition.rs (L908-925)
```rust
    /// Verifies the list of transaction infos are correct using the proof. The verifier
    /// needs to have the ledger info and the version of the first transaction in possession.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_info_version: Option<Version>,
    ) -> Result<()> {
        let txn_info_hashes: Vec<_> = self
            .transaction_infos
            .iter()
            .map(CryptoHash::hash)
            .collect();
        self.ledger_info_to_transaction_infos_proof.verify(
            ledger_info.transaction_accumulator_hash(),
            first_transaction_info_version,
            &txn_info_hashes,
        )
    }
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
