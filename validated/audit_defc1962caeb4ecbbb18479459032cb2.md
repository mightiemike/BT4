## Title
Restore path commits write sets without verifying them against the accumulator-proven `TransactionInfo.state_change_hash` - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
The Audius report's root cause is that a value trusted to be constrained by one invariant (`minDelegationAmount`) was actually enforced against the wrong scope, letting attacker-supplied data slip past the intended guard. The Aptos analog: the transaction-restore path verifies transactions, transaction infos, and events against the ledger-info-anchored accumulator, but never verifies that the `write_set` loaded from the backup archive matches the `state_change_hash` recorded in the (verified) `TransactionInfo`. The write set — the actual state mutation applied to the ledger — is the one field that is silently trusted and committed unchecked.

### Finding Description
In `LoadedChunk::load` (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs:99-186`), `write_sets` are deserialized from the backup file independently of `txns`, `txn_infos`, and `events`: [1](#0-0) 

The verification step then builds a `TransactionListWithProofV2` that does **not** include `write_sets` at all, and calls `.verify(...)`: [2](#0-1) 

`TransactionList::verify` (used here) only checks: (1) first version matches, (2) `txn_infos.len()` matches `txns.len()`, (3) `txn.committed_hash() == txn_info.transaction_hash()`, (4) the accumulator proof binds `txn_infos` to the `ledger_info`, and (5) event root hashes match — it never touches `write_set` or `state_change_hash`: [3](#0-2) 

By contrast, the sibling structure `TransactionOutputListWithProof::verify` (used elsewhere, e.g. state-sync output lists) explicitly re-hashes the write set and checks it against `txn_info.state_change_hash()`: [4](#0-3) 

That check is absent from the restore flow. After `LoadedChunk::load` returns, the unverified `write_sets` are used in two places without ever independently checking them against `txn_info.state_change_hash()`:

1. `save_before_replay_version` persists write sets directly to the DB via `restore_handler.save_transactions`: [5](#0-4) 
2. `replay_kv` persists write sets and *replays them into the state tree* via `save_transactions_and_replay_kv` → `restore_utils::save_transactions_impl`, which calls `state_store.calculate_state_and_put_updates` directly off the write sets, with no state_change_hash cross-check: [6](#0-5) 

`RestoreHandler::save_transactions` / `save_transactions_and_replay_kv` simply forward to `restore_utils::save_transactions` with no validation step in between: [7](#0-6) 

The only place a full write-set/`state_change_hash` cross-check happens is in the *fully-replayed* path where the chunk executor re-executes the VM and calls `ensure_match_transaction_info`, which does compare `CryptoHash::hash(self.write_set()) == txn_info.state_change_hash()`: [8](#0-7)  — but that path is used only when transactions are actually re-executed by the VM (`replay_transactions`/`verify_execution`), not for the "save before replay" fast path or the "kv-only replay" fast path, both of which trust the raw `write_sets` from the backup manifest.

### Impact Explanation
Both the "save before replay" and "kv-only replay" restore paths accept `write_sets` from the backup archive as authoritative and commit/replay them into durable storage (transaction DB, write-set DB, and even the Jellyfish Merkle state tree in the kv-replay case) with no cryptographic binding to the ledger-info-anchored `TransactionInfo.state_change_hash()`. A corrupted or tampered backup archive — e.g., truncated/mismatched write-set records due to storage bit-rot, a bug in a backup-producing tool, or a compromised/malicious backup storage backend supplying `BackupStorage` — can inject an arbitrary write set for a transaction whose hash, status, and event root are otherwise legitimately proven. This directly produces "committed state that differs from the correct VM result / corrupts durable ledger data," and any subsequent state read, state proof, or hot-state root derived from that corrupted version inherits the wrong values. Since these restore code paths are exactly the ones AIP-listed as in-scope ("restore flows," "state view," "proof binding"), and the resulting corruption silently passes all remaining verification (transaction hash, event root, accumulator proof to ledger_info all still validate), this is a High-severity integrity break in restored ledger state.

### Likelihood Explanation
This does not require a fully malicious BFT validator; it only requires an unreliable, buggy, or compromised backup source (self-hosted S3/GCS bucket, local file corruption, or a third-party backup-sharing peer in state-sync v2's bootstrap-from-backup flows). Given restore/state-sync-v2 bootstrap commonly pulls backups from operator-controlled or shared infrastructure not co-signed by validators, and the missing check is a straightforward omission (mirrored correctly in the neighboring `TransactionOutputListWithProof::verify`), the likelihood of this gap being hit — accidentally or via a compromised backup channel — is non-trivial for any node performing a "save-before-replay" or "kv-only" restore.

### Recommendation
Add a `write_set_hash == txn_info.state_change_hash()` check for every `(write_set, txn_info)` pair in `LoadedChunk::load` (mirroring `TransactionOutputListWithProof::verify`) before returning verified data from the backup chunk, and/or add the same check inside `restore_utils::save_transactions_impl` right before writing to `WriteSetDb` / before calling `calculate_state_and_put_updates`, so no restore code path can persist or replay a write set that isn't bound to the accumulator-proven `TransactionInfo`.

### Proof of Concept
1. Produce/modify a transaction backup chunk file so that a given record's `write_set` differs from the one used to compute the transaction's `state_change_hash` in `txn_info`, while leaving `txn`, `txn_info`, and `events` intact/consistent with the real, verifiable ledger_info-anchored accumulator proof.
2. Run `TransactionRestoreController` (or state-sync-v2 bootstrap-from-backup) either without `--replay-transactions-from-version` (hits `save_before_replay_version`) or with `--kv-only-replay=true` (hits `replay_kv`).
3. Observe that `LoadedChunk::load`'s `txn_list_with_proof.verify(...)` succeeds (it never inspects `write_sets`), and the tampered write set is written to `WriteSetDb`/state tree via `restore_utils::save_transactions_impl`, producing a durable ledger state at that version that diverges from what the transaction actually should have produced, with the on-chain `TransactionInfo.state_change_hash` in storage now describing a write set that does not match what's stored under `WriteSetSchema`/the resulting state values.

Note: I was not able to execute this end-to-end in a live environment; the analysis is based on static code tracing across `restore.rs`, `restore_utils.rs`, `restore_handler.rs`, and `types/src/transaction/mod.rs`. If desired, a Devin session with full repo/test access could add a unit test to `storage/backup/backup-cli` exercising `LoadedChunk::load` with an intentionally mismatched `write_set` to confirm `verify()` currently returns `Ok(())`.

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L156-169)
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
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L497-518)
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
                        let last_saved = first_version + num_to_save as u64 - 1;
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
