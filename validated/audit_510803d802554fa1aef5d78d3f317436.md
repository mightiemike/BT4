## Finding: Backup restore commits unauthenticated write sets without verifying them against `TransactionInfo::state_change_hash`

### Title
Backup/restore pipeline persists and replays write sets that are never cryptographically bound to the proven `TransactionInfo`, allowing a corrupted backup to produce a divergent restored ledger state - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
The `TransactionOutputListWithProof::verify` path used during normal state sync explicitly checks that `CryptoHash::hash(&txn_output.write_set) == txn_info.state_change_hash()` before a write set is trusted [1](#0-0) . However, the backup/restore code path for transactions does not perform this equivalent check: `write_sets` are parsed straight from the (untrusted) backup blob and are never validated against `state_change_hash` before being written to the `WriteSetDb` or used to recompute state during KV replay.

### Finding Description
`LoadedChunk::load` reads a backup chunk record containing `(txn, aux_info, txn_info, events, write_set)` tuples directly from `BackupStorage` [2](#0-1) . It then builds a `TransactionListWithProofV2` from the `txns`, `events`, and `txn_infos` (deliberately **excluding** `write_sets`) and calls `.verify(...)` against the epoch-authenticated ledger info [3](#0-2) . Because `write_sets` is never part of the structure being verified, this call only proves that transactions/events/status/gas match the accumulator-committed `TransactionInfo`s — it says nothing about the `write_sets` vector that was parsed alongside them from the same untrusted file.

The unverified `write_sets` are carried forward as a plain, un-authenticated local variable through `LoadedChunk` and eventually reach `RestoreHandler::save_transactions` / `save_transactions_and_replay_kv`, which call into `restore_utils::save_transactions_impl` [4](#0-3) [5](#0-4) . This function:
1. Writes the raw `write_sets` directly to `WriteSetDb` with no comparison to `txn_info.state_change_hash()` [6](#0-5) .
2. When `kv_replay` is enabled, feeds these same unverified `write_sets` into `state_store.calculate_state_and_put_updates(...)` to derive and persist the actual JellyfishMerkleTree state values that become the durable ledger state [7](#0-6) .

By contrast, the normal (non-backup) state-sync path enforces exactly the missing invariant — `TransactionOutputListWithProof::verify` checks `txn_info.state_change_hash() == write_set_hash` for every transaction before the output is accepted [8](#0-7) , and `TransactionOutput::ensure_match_transaction_info` (used by chunk-executor replay-verification) performs the same check [9](#0-8) . This confirms the check is a recognized, load-bearing integrity invariant elsewhere in the codebase — but it is absent from the backup-restore ingestion path when `VerifyExecutionMode::NoVerify` is used (the default fast-restore mode, see `VerifyExecutionMode::NoVerify` passed by `RestoreCoordinator` at both restore phases) [10](#0-9) [11](#0-10) .

### Impact Explanation
If the backup storage (`BackupStorage`) is compromised, corrupted, or serves a tampered chunk (e.g. supply-chain compromise of a backup provider, bit-rot, or an operator restoring from an unofficial backup source), a node performing `db-restore` / `db-tool replay-verify` in `NoVerify` mode will:
- Persist a `write_set` in `WriteSetDb` that does not correspond to the transaction actually proven by the accumulator (`transaction_info` is authentic, but the associated state delta is forged).
- Under `kv_replay`, apply that forged write set to compute new Jellyfish Merkle Tree state values and roots, meaning the restored node's live state (account balances, resources, etc.) silently diverges from the state any honest full-execution node would compute for the same `TransactionInfo`/version.

This is exactly the "committed state that differs from the correct VM result / corrupts durable ledger data" category called out by the state-commitment gate, since the corrupted state becomes the authoritative value served by later `get_state_value_with_proof_by_version` API calls (bound to a version and root that look legitimate to callers).

### Likelihood Explanation
Exploitation requires control over, or corruption of, the backup artifact retrieved by the restoring node's `BackupStorage`, and requires the restore to run without verify-execution (the default/fast path, `VerifyExecutionMode::NoVerify`). This is a real operational configuration (used by default in `RestoreCoordinator`), not a hypothetical one, so likelihood is non-trivial for operators who restore from third-party or less-trusted backup storage, though it does not affect consensus-driven mainnet execution/commit paths directly — its blast radius is confined to nodes performing DB restore/replay from backups.

### Recommendation
In `LoadedChunk::load` (and/or `save_transactions_impl`), verify each write set's hash against the corresponding `TransactionInfo::state_change_hash()` before accepting it — mirroring the check already performed in `TransactionOutputListWithProof::verify` and `TransactionOutput::ensure_match_transaction_info`. This should be enforced unconditionally (not gated behind `VerifyExecutionMode::should_verify()`), since it is a cheap hash comparison, not a full VM re-execution.

### Proof of Concept
Conceptual PoC (requires ability to serve a crafted backup chunk):
1. Take a legitimate transaction backup chunk (`txns`, `txn_infos`, `events`, `write_sets`, `proof`) for a target version `V`.
2. Replace the `write_set` at index `i` with an arbitrary alternate `WriteSet` (e.g. modifying an account balance), while leaving `txn_infos[i]` (and thus its `state_change_hash`, and the accumulator `proof`) untouched.
3. Serve this modified chunk via `BackupStorage` to a node running `aptos-db-tool restore` (or `replay-verify`) with default (`NoVerify`) settings.
4. Observe that `LoadedChunk::load`'s call to `txn_list_with_proof.verify(...)` succeeds (it never inspects `write_sets`), and that `save_transactions_impl` persists the tampered `write_set` to `WriteSetDb` and, under `kv_replay`, to the state tree — producing a restored state that differs from the one computed by honest execution of the same transaction, while the on-disk `TransactionInfo`/accumulator proof remain unchanged and still "verify".

**Caveat / uncertainty:** I was not able to fully trace every call path that constructs `TransactionListWithProofV2`/`TransactionListWithProof` (e.g., whether any wrapper elsewhere re-validates `write_sets` before they reach `save_transactions_impl`) due to tool/iteration limits, nor confirm whether `VerifyExecutionMode::should_verify()` paths (used in `chunk_executor`'s `verify_execution`, which does call `ensure_match_transaction_info`) are always exercised in the default restore CLI flow versus only in `replay-verify`. This should be double-checked in a full session before treating the finding as conclusively exploitable end-to-end in every restore configuration.

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

**File:** types/src/transaction/mod.rs (L2939-2984)
```rust

    /// Verifies the transaction output list with proof using the given `ledger_info`.
    /// This method will ensure:
    /// 1. All transaction infos exist on the given `ledger_info`.
    /// 2. If `first_transaction_output_version` is None, the transaction output list is empty.
    ///    Otherwise, the list starts at `first_transaction_output_version`.
    /// 3. Events, gas, write set, status in each transaction output match the expected event root hashes,
    ///    the gas used and the transaction execution status in the proof, respectively.
    /// 4. The transaction hashes match those of the transaction infos.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_output_version: Option<Version>,
    ) -> Result<()> {
        // Verify the first transaction output versions match
        ensure!(
            self.get_first_output_version() == first_transaction_output_version,
            "First transaction and output version ({:?}) doesn't match given version ({:?}).",
            self.get_first_output_version(),
            first_transaction_output_version,
        );

        // Verify the lengths of the transactions and outputs match the transaction infos
        ensure!(
            self.proof.transaction_infos.len() == self.get_num_outputs(),
            "The number of TransactionInfo objects ({}) does not match the number of \
             transactions and outputs ({}).",
            self.proof.transaction_infos.len(),
            self.get_num_outputs(),
        );

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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L192-229)
```rust
/// A helper function that saves the transactions to the given change set
pub(crate) fn save_transactions_impl(
    state_store: Arc<StateStore>,
    ledger_db: Arc<LedgerDb>,
    first_version: Version,
    txns: &[Transaction],
    persisted_aux_info: &[PersistedAuxiliaryInfo],
    txn_infos: &[TransactionInfo],
    events: &[Vec<ContractEvent>],
    write_sets: &[WriteSet],
    ledger_db_batch: &mut LedgerDbSchemaBatches,
    state_kv_batches: &mut ShardedStateKvSchemaBatch,
    kv_replay: bool,
) -> Result<()> {
    for (idx, txn) in txns.iter().enumerate() {
        ledger_db.transaction_db().put_transaction(
            first_version + idx as Version,
            txn,
            /*skip_index=*/ false,
            &mut ledger_db_batch.transaction_db_batches,
        )?;
    }

    for (idx, aux_info) in persisted_aux_info.iter().enumerate() {
        PersistedAuxiliaryInfoDb::put_persisted_auxiliary_info(
            first_version + idx as Version,
            aux_info,
            &mut ledger_db_batch.persisted_auxiliary_info_db_batches,
        )?;
    }

    for (idx, txn_info) in txn_infos.iter().enumerate() {
        TransactionInfoDb::put_transaction_info(
            first_version + idx as Version,
            txn_info,
            &mut ledger_db_batch.transaction_info_db_batches,
        )?;
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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L267-275)
```rust
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

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L286-300)
```rust
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

**File:** storage/backup/backup-cli/src/coordinators/restore.rs (L360-371)
```rust
            TransactionRestoreBatchController::new(
                self.global_opt,
                self.storage,
                txn_manifests,
                first_version,
                replay_version,
                epoch_history,
                VerifyExecutionMode::NoVerify,
                None,
            )
            .run()
            .await?;
```
