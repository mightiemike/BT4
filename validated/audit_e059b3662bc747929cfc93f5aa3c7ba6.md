### Title
Backup/restore write-set is never bound to the transaction's committed state-change hash - ([File: storage/backup/backup-cli/src/backup_types/transaction/restore.rs])

### Summary
When restoring a transaction backup, `LoadedChunk::load` reads a `(Transaction, PersistedAuxiliaryInfo, TransactionInfo, Vec<ContractEvent>, WriteSet)` tuple per record, but only the transaction, events, and `TransactionInfo` are folded into a `TransactionListWithProofV2` and cryptographically verified against the accumulator/ledger info. The `write_set` is carried alongside, completely out-of-band from that proof, and is never checked against `TransactionInfo::state_change_hash()` before being persisted to `WriteSetDb` (and, in KV-replay restore, fed into JMT state recomputation).

### Finding Description
`LoadedChunk::load` in [1](#0-0)  deserializes each backup record into `(txn, aux_info, txn_info, events, write_set)`. It then builds a `TransactionListWithProofV2`/`TransactionListWithProof` (which contains only `transactions`, `events`, and the accumulator-proven `transaction_infos`, but not write sets) and calls `.verify(...)`: [2](#0-1) 

Looking at `TransactionListWithProofV2`'s underlying `verify` implementation, it only checks that (1) transaction hashes match `txn_info.transaction_hash()`, (2) event root hashes match `txn_info.event_root_hash()`, and (3) `txn_info`s are proven by the accumulator against `ledger_info`: [3](#0-2) 

Nowhere in this path is `write_set` hashed and compared to `txn_info.state_change_hash()`. Contrast this with `TransactionOutputListWithProof::verify`, which explicitly performs that binding for a different (output-based) list type: [4](#0-3) 

After `LoadedChunk::load` returns, the unverified `write_sets` are unpacked and handed to the DB-writing helper `restore_utils::save_transactions`, which persists them directly into `WriteSetDb` via `WriteSetDb::put_write_set`, and — when KV replay is requested — uses them to recompute and commit the actual Jellyfish Merkle Tree state: [5](#0-4) 

Because the transaction, its `TransactionInfo`, and its events are the only fields anchored to the accumulator-proven ledger info, an attacker who can tamper with the transaction backup chunk file (e.g., a compromised/malicious backup storage backend, MITM on backup transport, or a corrupted archival source used for `replay-verify`/state restore) can substitute an arbitrary, self-consistent `WriteSet` for any transaction in the chunk while leaving the transaction, events, and `TransactionInfo` untouched. Since none of those forged bytes are cross-checked against `txn_info.state_change_hash()`, `LoadedChunk::load`'s `verify()` call succeeds and the forged write set is committed as if it were the authentic VM output for that version.

### Impact Explanation
This breaks the "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" invariant. The corrupted `WriteSet` becomes the durable, queryable record for that version in `WriteSetDb`, and in KV-replay-based restores it directly corrupts the recomputed state tree/root via `calculate_state_and_put_updates`, producing committed ledger state that diverges from the correct VM result without any accumulator/proof failure being raised. This is a high-severity state-commitment integrity break matching the "committed state differs from correct VM result / corrupts durable ledger data" impact class.

### Likelihood Explanation
Exploitability depends on the attacker's ability to control or tamper with backup chunk files that a restoring node consumes (this is precisely the threat model the accumulator-proof verification in this file is designed to defend against — otherwise there would be no reason to verify transactions/events against the ledger info at all during restore). Given that Aptos backups are commonly stored in cloud object storage and consumed by third parties running `replay-verify`/state restore, and this specific field is silently excluded from the proof check while sibling fields are covered, the likelihood of triggering via a compromised or MITM'd backup source is realistic.

### Recommendation
In `LoadedChunk::load`, after building/verifying the `TransactionListWithProofV2`, additionally hash each `write_set` and assert it equals the corresponding `txn_info.state_change_hash()` (mirroring the check already implemented in `TransactionOutputListWithProof::verify`) before allowing `write_sets` to be unpacked and passed to `save_transactions`/replay.

### Proof of Concept
1. Take a valid transaction backup chunk file (record format `(txn, aux_info, txn_info, events, write_set)`).
2. Replace only the `write_set` bytes for one record with an attacker-chosen `WriteSet` (e.g., crediting an arbitrary account), leaving `txn`, `aux_info`, `txn_info`, and `events` untouched.
3. Feed this tampered chunk to `TransactionRestoreController`/`LoadedChunk::load`. The `TransactionListWithProofV2::verify` call succeeds because it never inspects `write_set`.
4. The forged `write_set` is persisted via `restore_utils::save_transactions` → `WriteSetDb::put_write_set`, and (if KV replay is enabled) is applied to compute a new, wrong state root/commitment, corrupting the target database despite passing all proof checks.

Note: I was unable to fully inspect `storage/aptosdb/src/backup/restore_handler.rs`'s `save_transactions` wrapper (the grep for it did not match in the indexed content), so I cannot rule out an additional guard at that layer beyond what `restore_utils.rs` shows; based on the code I could directly read, no such check exists before the write set reaches persistent storage.

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
