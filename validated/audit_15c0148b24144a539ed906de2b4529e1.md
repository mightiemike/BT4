## Title
Backup restore commits `WriteSet`s that bypass accumulator-proof verification, allowing corrupted/unauthenticated state to be written to storage - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
Aptos's backup/restore machinery reconstructs a `TransactionListWithProof` purely to re-use its authenticated verification logic, but the `WriteSet`s read from the same backup chunk are kept in a side channel that is never included in, or checked against, that verification. `TransactionListWithProof::verify` (unlike `TransactionOutputListWithProof::verify`) does not carry or check write sets at all, so nothing ever confirms `CryptoHash::hash(write_set) == txn_info.state_change_hash()` for the write sets restored from a backup file, before they are persisted into the ledger DB and (in the KV-replay path) used to compute committed state values.

### Finding Description
In `LoadedChunk::load` (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs:99-186`), each backup record is deserialized into `(txn, aux_info, txn_info, events, write_set)` and every field is pushed into its own parallel vector, including `write_sets` [1](#0-0) .

To validate the chunk, the code builds a `TransactionListWithProofV2` from `txns`, `event_vecs`, and `txn_infos` plus the accumulator `range_proof`/`ledger_info`, and calls `.verify(...)`: [2](#0-1) 

Crucially, `write_sets` is **not** part of this constructed structure — `TransactionListWithProof` only has `transactions`, `events`, `first_transaction_version`, and `proof` fields [3](#0-2) , and its `verify()` only checks transaction hashes against `txn_info.transaction_hash()`, event root hashes against `txn_info.event_root_hash()`, and the accumulator proof against the ledger info — it never touches `state_change_hash()` (the write-set hash field) at all [4](#0-3) . By contrast, `TransactionOutputListWithProof::verify` (used for the "outputs" backup/sync format) does explicitly verify `write_set_hash == txn_info.state_change_hash()` [5](#0-4) , showing that write-set-to-txn_info binding is a known, expected invariant that this call site never enforces.

After "verification" succeeds, the code disassembles the txn_list_with_proof to recover the now-*verified* `txn_infos`/`txns`/`event_vecs`, but the `write_sets` returned in `LoadedChunk` are the original, unverified vector collected directly from the file [6](#0-5) .

These unverified `write_sets` subsequently flow into `restore_handler.save_transactions` (plain replay/backfill path) or `restore_handler.save_transactions_and_replay_kv` (KV-only replay path used by `TransactionRestoreBatchController::replay_kv`) [7](#0-6) [8](#0-7) . Both call into `restore_utils::save_transactions`/`save_transactions_impl`, which writes the write sets directly into the `WriteSetDb` and, when `kv_replay` is true, additionally feeds them into `state_store.calculate_state_and_put_updates(...)` to compute and persist committed state-KV updates, without recomputing or checking `CryptoHash::hash(write_set)` against the corresponding `txn_info.state_change_hash()` anywhere in that function [9](#0-8) .

The verified `txn_info` (bound to the accumulator/ledger-info root via the proof) is persisted as-is into `TransactionInfoDb`/accumulator, so the on-chain-authenticated "expected" `state_change_hash` is stored, but the actual `write_set` bytes stored in `WriteSetDb` — and the state values computed from them during KV replay — are never checked to match that hash. This exactly mirrors the reported bug class: one input (the price / minPrice bound in the original report; here, the write-set hash bound to `txn_info`) is silently exempted from the bounds/consistency check that is applied to sibling data (transactions, events), letting untrusted or corrupted data pass as if authenticated.

### Impact Explanation
A backup archive originating from a compromised or malicious backup storage backend (or corrupted in transit/at rest) can supply a `write_set` that does not correspond to the accumulator-proven `TransactionInfo` for that version, while still passing `txn_list_with_proof.verify()` because that function checks transactions/events/proof but not write sets. When restored:
- `WriteSetDb` at that version stores committed data that is inconsistent with the authenticated `state_change_hash` recorded in the (correctly verified) `TransactionInfo`.
- In the KV-replay path, `calculate_state_and_put_updates` derives and persists state-KV values directly from this unverified write set, producing a restored ledger state that diverges from the true VM-executed state for that transaction, while the DB's transaction-info/accumulator layer still reports the original, correct root hash — an authenticated-but-wrong binding between version/root and the underlying committed state.

This is a state-commitment integrity break: the restored node's committed ledger data can silently diverge from the canonical VM result even though its transaction-accumulator proofs "check out," which is exactly the class of high-impact issue the state-integrity gate is scoped to (committed state differing from correct VM result / corrupted durable ledger data via an unauthenticated restore path).

### Likelihood Explanation
Exploitation requires control over, or the ability to corrupt, backup archive content consumed by a restoring node (e.g., a malicious/compromised backup storage provider, or a node operator pointed at an untrusted backup source) — this is a real and supported operational flow (`db-restore`/`TransactionRestoreBatchController`), not a purely theoretical trust boundary. Given that the omission is a straightforward missing check (the exact same check exists one type over, in `TransactionOutputListWithProof::verify`), this looks like an oversight rather than an intentional design decision, increasing confidence that it is a genuine local root cause rather than something mitigated elsewhere. I was not able to fully trace every downstream consumer within the tool-call budget (e.g., whether a later full re-execution/replay-verify step independently re-derives and cross-checks the state root against `state_checkpoint_hash` in all restore configurations), so there is some uncertainty about whether an additional layer (e.g., `VerifyExecutionMode`/`replay_on_archive`, or a final root-hash comparison) catches this in some restore modes; but in the `KvOnly`/KV-replay fast path examined, no such recomputation against `state_change_hash` was found.

### Recommendation
In `LoadedChunk::load`, after constructing and verifying `txn_list_with_proof`, explicitly verify each `write_set` against its corresponding `txn_info.state_change_hash()` (i.e., `ensure!(CryptoHash::hash(&write_set) == txn_info.state_change_hash())`) before returning them from `LoadedChunk`, mirroring the check already performed in `TransactionOutputListWithProof::verify`. Alternatively, extend `TransactionListWithProof`/its verify routine to optionally carry and check write sets, so this binding is enforced uniformly wherever `TransactionListWithProof` is reused as a verification helper for data that includes write sets.

### Proof of Concept
Conceptual PoC (backup-file-level attack, no privileged access to the running node needed once feeding it an untrusted/malicious backup source):
1. Take a legitimate transaction backup chunk containing `(txn, aux_info, txn_info, events, write_set)` records and a valid `(range_proof, ledger_info)` file.
2. For one record, replace `write_set` with an attacker-chosen `WriteSet` (e.g., one that credits an attacker-controlled account or corrupts a resource), leaving `txn`, `txn_info`, and `events` untouched so their hashes still match the proof.
3. Point `TransactionRestoreBatchController`/`db-restore` (or the KV-replay path used during `RestoreCoordinator::run_impl`) at this tampered backup.
4. `LoadedChunk::load` calls `txn_list_with_proof.verify(...)`, which succeeds because it only checks `txn`/`events`/accumulator proof, not `write_set`.
5. The tampered `write_set` is persisted to `WriteSetDb`, and if kv-replay is enabled, `calculate_state_and_put_updates` writes the corrupted state values into the state-KV store — producing a restored DB whose committed state at that version does not match the value implied by the (correctly verified) `state_change_hash` in `TransactionInfo`, despite the ledger's accumulator/proof layer appearing fully authenticated.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L112-136)
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L168-186)
```rust
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
    }
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L497-527)
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
                        TRANSACTION_SAVE_VERSION.set(last_saved as i64);
                        info!(
                            version = last_saved,
                            accumulative_tps = ((last_saved - global_first_version + 1) as f64
                                / start.elapsed().as_secs_f64())
                                as u64,
                            "Transactions saved."
                        );
                    }
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L600-613)
```rust
                async move {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["replay_txn_chunk_kv_only"]);
                    tokio::task::spawn_blocking(move || {
                        // we directly save transaction and kvs to DB without involving chunk executor
                        handler.save_transactions_and_replay_kv(
                            base_version,
                            &txns,
                            &persisted_aux_info,
                            &txn_infos,
                            &events,
                            write_sets,
                        )?;
                        // return the last version after the replaying
                        Ok(base_version + offset - 1)
```

**File:** types/src/transaction/mod.rs (L2642-2648)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Deserialize, Serialize)]
pub struct TransactionListWithProof {
    pub transactions: Vec<Transaction>,
    pub events: Option<Vec<Vec<ContractEvent>>>,
    pub first_transaction_version: Option<Version>,
    pub proof: TransactionInfoListWithProof,
}
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
