Based on my investigation, I found a concrete, exploitable analog to the reported bug class: a struct with fields not covered by its own `verify()` function, allowing content to be substituted without proof invariant violation.

### Title
Transaction backup restore commits `WriteSet` bytes to durable storage without verifying them against the accumulator-proven `TransactionInfo.state_change_hash` - ([File: storage/backup/backup-cli/src/backup_types/transaction/restore.rs])

### Summary
`TransactionListWithProof` (and its `TransactionListWithProofV2`/`TransactionListWithAuxiliaryInfos` wrappers) only contains `transactions`, `events`, and `proof` (transaction infos + accumulator range proof) — it has no `write_set` field at all. [1](#0-0) 
Its `verify()` checks transaction hashes against `txn_info.transaction_hash()`, checks event roots against `txn_info.event_root_hash()`, and checks the accumulator proof — but never touches a write set. [2](#0-1) 

### Finding Description
In backup transaction restore, `LoadedChunk::load` decodes each record as a 5-tuple `(txn, aux_info, txn_info, events, write_set)` from raw backup bytes. [3](#0-2) 
It then constructs a `TransactionListWithProofV2` using only `txns`, `event_vecs`, and `txn_infos` (the `write_sets` vector is kept aside, outside the structure that gets verified) and calls `.verify(...)`. [4](#0-3) 
Since `TransactionListWithProof::verify` never hashes `write_set` or compares it to `txn_info.state_change_hash()` (unlike `TransactionOutputListWithProof::verify` and `TransactionOutput::ensure_match_transaction_info`, which do perform this exact check) [5](#0-4) [6](#0-5) 
the `write_sets` array that was decoded from the same untrusted backup file is never checked for integrity before being written to durable storage. `save_transactions`/`save_transactions_impl` persist the `write_sets` directly into `WriteSetDb` via `WriteSetDb::put_write_set`, with no consistency check against `txn_info.state_change_hash()` anywhere in that path. [7](#0-6) 
The only thing binding the version's identity to a Merkle proof is `txn_info`/`txn` hash — the `write_set` bytes accompanying it are essentially "static data placed after a validated boundary" that is trusted implicitly, exactly analogous to how the 1inch bug let a maker's dynamic bytes silently override an assumed-safe static field without the decoder catching it.

### Impact Explanation
If a backup archive (or any component that produces `TransactionChunk` files — e.g. a compromised/buggy backup-generation node, a corrupted/tampered backup storage medium, or a bug in backup creation) supplies a `write_set` that does not match the one that was actually executed and proven at that version, `TransactionRestoreController`/`TransactionRestoreBatchController` will commit incorrect state-changes into `WriteSetDb`. Because `WriteSetDb` and the JMT/state values are derived from this same input during KV-replay (`save_transactions_impl` calls `state_store.calculate_state_and_put_updates` using `write_sets` when `kv_replay` is true), an attacker-controlled or corrupted `write_set` can inject wrong state values into the restored ledger, corrupting durable state while still passing all proof checks the code performs. This directly matches the "Committed state that differs from the correct VM result or corrupts durable ledger data" and "Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state" impact criteria.

### Likelihood Explanation
Exploitation requires control over the bytes of a `TransactionChunk`/backup transaction file that is fed into restore or KV-replay, i.e., an untrusted or compromised backup source. This is a real, supported restore path (`aptos db-tool`/backup-cli), not test-only code, and the check that IS present for `TransactionOutputListWithProof` shows the codebase authors consider write-set/txn-info-hash consistency to be part of the required invariant — its absence in the `TransactionListWithProof` restore path looks like an accidental omission rather than an intentional design choice.

### Recommendation
In `TransactionListWithProof::verify` (or specifically in the transaction-restore/replay path that carries `write_sets` alongside `txn_infos`), add a check that `CryptoHash::hash(write_set) == txn_info.state_change_hash()` for every transaction, mirroring the check already performed in `TransactionOutputListWithProof::verify` and `TransactionOutput::ensure_match_transaction_info`, before `write_sets` are persisted by `restore_utils::save_transactions`/`save_transactions_impl`.

### Proof of Concept
1. Take a legitimate backup `TransactionChunk` (transactions file + manifest + `TransactionAccumulatorRangeProof`/`LedgerInfoWithSignatures`).
2. Re-serialize one record, keeping `txn`, `txn_info`, and `events` identical (so `committed_hash()`/`event_root_hash` checks pass and the accumulator proof still validates), but substitute a different `write_set` (e.g., one that credits an arbitrary account additional balance) whose BCS-serialized hash differs from `txn_info.state_change_hash()`.
3. Feed this tampered chunk into `TransactionRestoreController`/`replay_kv`. `LoadedChunk::load` will decode and pass `txn_list_with_proof.verify(...)` successfully (per [4](#0-3) ) because that verification never inspects `write_set`.
4. `save_transactions`/`save_transactions_impl` commits the malicious `write_set` to `WriteSetDb` and (when `kv_replay` is set) applies it to state via `calculate_state_and_put_updates`, corrupting the restored ledger's state while the on-disk `TransactionInfo`/accumulator proof still (falsely) appears self-consistent.

### Citations

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

**File:** types/src/transaction/mod.rs (L2700-2752)
```rust
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

**File:** types/src/transaction/mod.rs (L2971-2984)
```rust
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L156-167)
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
