## Title
Backup restore accepts a `WriteSet` that is never checked against the accumulator-proven `TransactionInfo.state_change_hash`, allowing forged committed state during `kv_replay` restore - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
The transaction-backup restore path loads `(Transaction, PersistedAuxiliaryInfo, TransactionInfo, ContractEvent list, WriteSet)` tuples straight from backup-storage-provided bytes and verifies only the `Transaction` and `events` fields against the ledger-accumulator-proven `TransactionInfo`. The `WriteSet` is never checked against `TransactionInfo::state_change_hash()`, yet in KV-only replay mode this unverified `WriteSet` is written directly into `WriteSetDb` and folded into the Jellyfish Merkle state via `calculate_state_and_put_updates`, becoming durable committed state.

### Finding Description
`LoadedChunk::load` in [1](#0-0)  deserializes `txns`, `txn_infos`, `event_vecs`, and `write_sets` from the same backup-storage record bytes, then only wraps `txns`/`event_vecs`/`txn_infos` into a `TransactionListWithProofV2` to reuse its verification: [2](#0-1) .

`TransactionListWithProof::verify` (the code actually invoked) checks the transaction hash against `txn_info.transaction_hash()` and the event root hash against `txn_info.event_root_hash()`, then verifies `TransactionInfo` inclusion via the accumulator proof — it never touches `write_set` or `state_change_hash`: [3](#0-2) .

For contrast, `TransactionOutputListWithProof::verify` (used elsewhere, e.g. in state-sync) explicitly hashes the write set and compares it to `txn_info.state_change_hash()`: [4](#0-3) . This proves the codebase's own security model treats `write_set.hash() == txn_info.state_change_hash()` as a mandatory integrity check whenever a write set is bound to a verified `TransactionInfo`. Backup restore, however, uses the `TransactionListWithProof` path (no write-set field at all) while separately smuggling in a raw `write_set` from the same untrusted byte stream, so this required check is silently skipped.

The unverified `write_sets` are then handed to `restore_handler.save_transactions_and_replay_kv`, which in KV-only replay mode calls `state_store.calculate_state_and_put_updates(&StateUpdateRefs::index_write_sets(...))` to directly derive and commit new state values/roots from the write set content, without VM re-execution: [5](#0-4) . The same unchecked `write_sets` are also persisted verbatim into `WriteSetSchema` via `WriteSetDb::put_write_set`: [6](#0-5) .

### Impact Explanation
Because the `TransactionInfo` is authenticated (via the ledger-info-signed accumulator proof) but the accompanying `WriteSet` is not bound to it, a backup storage provider (or any party able to tamper with the transaction-chunk file while leaving the separately-fetched proof/ledger-info file intact) can substitute an arbitrary `WriteSet` for a given transaction. During `kv-only` restore this forged write set is used to compute and commit new state (`calculate_state_and_put_updates`) and is stored durably in `WriteSetDb`, producing ledger state that diverges from what was actually agreed upon by consensus/VM execution — a direct "committed state differs from correct VM result / corrupts durable ledger data" outcome. This breaks the `state_change_hash` binding invariant that the rest of the codebase (transaction-output verification, VM output pipeline) treats as load-bearing.

### Likelihood Explanation
This requires an untrusted or compromised backup storage source, which is a plausible operational scenario for restore/verify tooling (backup files are commonly fetched from cloud storage, community mirrors, or various operators different from the validator itself) — the code is specifically designed to distrust storage.rs content and re-derive validity from proofs, but this one field escapes that model. The bug is deterministic and requires no timing race; it manifests only for the `kv_only_replay` restore path (`replay_kv`), not for full VM-replay restore (which recomputes and would presumably diverge, though this path was not fully traced for a post-hoc equality check against `state_change_hash`/`state_checkpoint_hash`).

### Recommendation
In `LoadedChunk::load`, after obtaining `txn_infos` and `write_sets`, add an explicit check `ensure!(CryptoHash::hash(write_set) == txn_info.state_change_hash())` for every transaction before they are returned/used, mirroring the check already performed in `TransactionOutputListWithProof::verify`. Alternatively, route backup replay through a proof type that includes the write set as part of the authenticated payload (or bundle `TransactionInfoListWithProof` verification with an explicit per-write-set hash check) so `kv_replay`/`save_transactions_and_replay_kv` never consumes unauthenticated state-changing data.

### Proof of Concept
1. Take a legitimate transaction backup chunk file and the associated genuine `(TransactionAccumulatorRangeProof, LedgerInfoWithSignatures)` proof file (proof file is untouched/valid).
2. In the chunk's serialized records, replace the `WriteSet` field of one record with an attacker-chosen `WriteSet` (leave `Transaction`, `PersistedAuxiliaryInfo`, `TransactionInfo`, and `events` untouched so `txn_hash`/`event_root_hash` checks and the accumulator proof in `LoadedChunk::load` still pass).
3. Run `aptos-db-restore` (or the internal restore controller) in `kv_only_replay` mode against this tampered chunk with `replay-transactions-from-version` covering the tampered version.
4. Observe that `LoadedChunk::load`'s `txn_list_with_proof.verify(...)` succeeds (it never inspects `write_set`), and `replay_kv` → `save_transactions_and_replay_kv` → `calculate_state_and_put_updates` commits state derived from the forged `WriteSet`, and `WriteSetDb` persists the forged write set at that version — producing a restored node whose state at that version differs from the actual consensus-committed state while `TransactionInfo`/ledger-info verification reported success.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L100-137)
```rust
    async fn load(
        manifest: TransactionChunk,
        storage: &Arc<dyn BackupStorage>,
        epoch_history: Option<&Arc<EpochHistory>>,
    ) -> Result<Self> {
        let mut file = BufReader::new(storage.open_for_read(&manifest.transactions).await?);
        let mut txns = Vec::new();
        let mut persisted_aux_info = Vec::new();
        let mut txn_infos = Vec::new();
        let mut event_vecs = Vec::new();
        let mut write_sets = Vec::new();

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L147-169)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2693-2734)
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
```

**File:** types/src/transaction/mod.rs (L2970-2984)
```rust
        // Verify the events, write set, status, gas used and transaction hashes.
        self.transactions_and_outputs.par_iter().zip_eq(self.proof.transaction_infos.par_iter())
        .map(|((txn, txn_output), txn_info)| {
            // Check the events against the expected events root hash
            verify_events_against_root_hash(&txn_output.events, txn_info)?;

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
