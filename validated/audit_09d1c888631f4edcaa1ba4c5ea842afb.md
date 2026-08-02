Based on my investigation, I found a credible analog in the **transaction backup restore path**, not the storage commit/accumulator code itself (which does check root hashes and version continuity — see `check_and_put_ledger_info` in `storage/aptosdb/src/db/aptosdb_writer.rs`).

### Title
Write sets restored from backup are persisted without being bound to `TransactionInfo.state_change_hash`, letting untrusted write-set content pass proof-based restore verification - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
`LoadedChunk::load` reads `txns`, `txn_infos`, `events`, and `write_sets` from a backup file, but only wraps `txns`/`events`/`txn_infos` into a `TransactionListWithProofV2` and calls `.verify(...)` against the trusted `LedgerInfoWithSignatures`. The `write_sets` vector is read separately and never included in, or cross-checked against, that proof verification. [1](#0-0) [2](#0-1) 

### Finding Description
Each `TransactionInfo` carries a `state_change_hash` field intended to commit to the write set produced by executing that transaction (referenced throughout `types/src/transaction/mod.rs` and consumed across the API/indexer layers). The accumulator range proof (`TransactionAccumulatorRangeProof`) verified in `LoadedChunk::load` only binds the hash of each `TransactionInfo` object to the ledger's accumulator root — it does not, and structurally cannot, validate the *contents* of a separately-supplied `write_sets` vector, since that vector is decoded straight from the backup record and passed through untouched: [3](#0-2) 

After this "verified" chunk is produced, the non-replay restore path forwards `write_sets` directly into durable storage via `RestoreHandler::save_transactions` → `restore_utils::save_transactions` → `save_transactions_impl`, which writes them straight into `WriteSetDb` with no check that `write_set.hash() == txn_info.state_change_hash()`: [4](#0-3) [5](#0-4) 

This mirrors the report's bug class exactly: a validation routine (`verify()` / `verifyThresholds`) exists and looks thorough, but omits one specific state field (write-set/state_change_hash vs. agreement-suspension status) that is trusted implicitly downstream, allowing an unprivileged/compromised backup-storage provider to substitute an arbitrary write set for a given version while all other proof checks (transaction hash, event hash, txn_info accumulator inclusion, ledger-info signatures) still pass.

### Impact Explanation
If accepted, this write set is committed as ground truth for that version in `write_set_db`, is later served through authenticated APIs/backup exports bound to that version and "verified" ledger info, and is also fed into `calculate_state_and_put_updates` during `kv_replay`, meaning it can corrupt the derived state tree for subsequent versions. This is a state-commitment integrity break: committed durable ledger data (write sets, and downstream state) can diverge from the actual VM-executed result while still appearing "proof-verified" to any restore-time consumer of `LoadedChunk`.

### Likelihood Explanation
Requires control over the (usually semi-trusted, but not full-consensus-privileged) backup storage backend supplying the archive read by `storage.open_for_read(&manifest.transactions)`; this is a plausible unprivileged/compromised-dependency scenario for restore/recovery/state-sync-from-backup operators, not a validator/consensus privilege. I was not able to fully confirm (due to tool-call limits) whether any other layer (e.g. `ChunkExecutor::enqueue_chunks` used in the `replay_transactions` path) re-derives and cross-checks the write set against re-execution before commit for the *replay* code path — only the direct `save_before_replay_version`/non-replay path was confirmed to skip this check.

### Recommendation
In `LoadedChunk::load`, after obtaining verified `txn_infos`, assert `CryptoHash::hash(&write_sets[i]) == txn_infos[i].state_change_hash()` (or the equivalent commitment field) for every index before returning the chunk, so `write_sets` are cryptographically bound to the already-proof-verified `TransactionInfo` list prior to being persisted by `save_transactions`.

### Proof of Concept
Not independently executable within this investigation's tool budget; the trace is code-level: (1) craft a backup transaction chunk file where `write_set` bytes for one record differ from the write set that produced the recorded `txn_info.state_change_hash`, while keeping `txn`/`events`/`txn_info` unchanged so the accumulator/ledger-info proof in `LoadedChunk::load` still validates; (2) run transaction restore; (3) observe the forged `write_set` land in `WriteSetDb` for that version via `save_transactions_impl`.

**Confidence caveat:** I confirmed the missing cross-check in the restore/verify code path with certainty, but did not have remaining tool calls to open the `TransactionInfo` struct definition directly to quote the exact `state_change_hash` field documentation, nor to fully trace whether `ChunkExecutor`'s replay path (used in `replay_transactions`) independently re-derives write sets from VM execution (which would mitigate, but not eliminate, the direct-save path's exposure). A Devin session with full file access should verify these two points before treating this as final.

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L139-185)
```rust
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

**File:** storage/aptosdb/src/backup/restore_handler.rs (L78-99)
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
