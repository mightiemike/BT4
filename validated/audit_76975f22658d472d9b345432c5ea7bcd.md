This request asks me to perform an open-ended vulnerability hunt across a large, security-sensitive codebase (transaction execution, storage commit, Merkle proofs, restore flows) based on an unrelated external bug report about a completely different protocol (an ERC-4626/ERC-5095 token slippage issue in Solidity). I'll do a genuine, bounded investigation of the analogous areas in Aptos, but I want to be upfront: without deep, sustained analysis across executor/storage/proof code, I cannot responsibly assert a high/critical finding — fabricating one would be irresponsible.

Let me look at a few of the pivots mentioned (write-set conversion, transaction info construction, proof verification) to see if anything concrete surfaces.## Finding

### Title
Restored write sets are never authenticated against the accumulator-proven `state_change_hash`, allowing corrupted/tampered backup data to be silently committed during transaction restore - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
The transaction-backup restore path loads `(txn, aux_info, txn_info, events, write_set)` tuples from a backup chunk file and verifies them using `TransactionListWithProof::verify()`, which only authenticates the transaction hash, event root hash, and the accumulator proof of the `TransactionInfo` list. It never checks that the loaded `write_set` hashes to `txn_info.state_change_hash()`. The unauthenticated `write_set` is then persisted directly to the ledger/state-KV databases via `save_transactions`, so a corrupted or substituted write set in the backup file is committed as if it were the authentic, proof-covered result.

### Finding Description
`LoadedChunk::load` in `storage/backup/backup-cli/src/backup_types/transaction/restore.rs` reads five values per record from the backup chunk: transaction, auxiliary info, `TransactionInfo`, events, and `WriteSet`. [1](#0-0) 

It builds a `TransactionListWithProofV2`/`TransactionListWithProof` — deliberately *without* the write sets — purely "to reuse its verification code", and calls `.verify(...)`: [2](#0-1) 

`TransactionListWithProof::verify` (in `types/src/transaction/mod.rs`) checks: (1) transaction hashes match `txn_info.transaction_hash()`, (2) the `TransactionInfo` list is authenticated by the accumulator proof against `ledger_info`, and (3) event root hashes match `txn_info.event_root_hash()` if events are present. It never touches `write_set` or `state_change_hash`: [3](#0-2) 

Compare this with the sibling `TransactionOutputListWithProof::verify`, used elsewhere, which *does* bind the write set to the proof: [4](#0-3) 

Because `LoadedChunk::load` chose the weaker `TransactionListWithProof` verification and kept `write_sets` as a side channel loaded straight from the untrusted file, the `write_set` returned by `LoadedChunk` is unauthenticated. It flows into `save_before_replay_version`, which for all transactions prior to any configured replay-from-version calls `restore_handler.save_transactions(...)` directly (no VM execution, no hash check): [5](#0-4) 

That handler ultimately reaches `save_transactions_impl` in `storage/aptosdb/src/backup/restore_utils.rs`, which writes the `TransactionInfo` (with its accumulator-proven `state_change_hash`) and the raw `write_set` to separate column families, and (in `kv_replay` mode) even derives new state values from the write sets — again with no comparison between `CryptoHash::hash(&write_set)` and the corresponding `txn_info.state_change_hash()`: [6](#0-5) 

The same unauthenticated `write_set` is used by the `kv_only_replay` fast path (`replay_kv`), which calls `save_transactions_and_replay_kv` directly, bypassing even the `ChunkExecutor`/`VerifyExecutionMode` machinery that would otherwise re-execute and compare outputs via `ensure_match_transaction_info` (which does check the write-set hash): [7](#0-6) [8](#0-7) 

The net effect: the accumulator/ledger-info proof authenticates the *`TransactionInfo`* (hence `state_change_hash`) as committed on-chain, but nothing in the restore path checks that the *write set bytes taken from the backup file* actually hash to that proven `state_change_hash`. The write set is therefore accepted purely on the trust that the backup storage returned matching files, with no cryptographic binding.

### Impact Explanation
An attacker who can tamper with or serve a corrupted transaction-backup chunk file (compromised backup storage, MITM on backup fetch, bit-flip/corruption, or a buggy backup writer) can substitute an arbitrary `WriteSet` for a historical transaction while keeping the same transaction and events, and the restore/replay-verify tooling will accept it as valid and commit it to the target node's ledger/state-KV databases. This corrupts durable ledger data at specific historical versions: state values and the Jellyfish Merkle tree entries built from `kv_replay` will silently diverge from the authentic chain state, even though the restored `TransactionInfo`/accumulator continues to show the legitimate `state_change_hash`. Any authenticated state read served later at that version off the corrupted database (state proof against the accumulator-derived transaction info, or DB-backed API responses) will be internally inconsistent between the on-disk state values and what the proof commitment says was written — a durable, hard-to-detect state-integrity break in backup/restore, one of the pivots explicitly in scope. This affects archive/full nodes rebuilt from backups and any replay-verify tooling used to validate historical correctness, since replay-verify (`go_through_verified_chunks`/save-only path) never performs the write-set hash check either.

### Likelihood Explanation
This requires control over or corruption of the backup source (not an on-chain/mainnet-consensus attacker), so it is not exploitable by an ordinary unprivileged network participant against a live validator. It is realistic wherever backups are fetched from third-party/cloud storage, mirrors, or CDNs that are not fully trusted, or where storage corruption/bugs occur, and it silently defeats an intended safety property of the restore/replay-verify pipeline (the whole point of carrying `TransactionInfo` + accumulator proof is to authenticate the write set that produced `state_change_hash`).

### Recommendation
In `LoadedChunk::load`, after (or instead of) building `TransactionListWithProof`, verify each loaded `write_set` against the corresponding `txn_info.state_change_hash()` (mirroring `TransactionOutputListWithProof::verify`/`ensure_match_transaction_info`) before returning them for persistence, in both the "save without replay" and `kv_only_replay` code paths.

### Proof of Concept
1. Perform a normal transaction backup of a range of versions with `TransactionBackupController`.
2. In the resulting backup chunk file, replace the serialized `WriteSet` bytes for one record with a different, but structurally valid, `WriteSet` (transaction, txn_info, and events left untouched so hashes still match).
3. Run `TransactionRestoreController`/`TransactionRestoreBatchController::run` (or the `replay-verify`/kv-only-replay flow) against this tampered manifest.
4. Observe that `LoadedChunk::load`'s call to `txn_list_with_proof.verify(...)` succeeds (it only checks `txn.committed_hash()` and event root hash), and that the tampered `write_set` is persisted via `save_transactions`/`save_transactions_and_replay_kv`, resulting in state values at that version that do not match `CryptoHash::hash(write_set) == txn_info.state_change_hash()`, with no error raised anywhere in the pipeline.

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L156-175)
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L553-618)
```rust
    // only apply KV to the DB
    async fn replay_kv(
        &self,
        restore_handler: &RestoreHandler,
        txns_to_execute_stream: impl Stream<
            Item = Result<(
                Transaction,
                PersistedAuxiliaryInfo,
                TransactionInfo,
                WriteSet,
                Vec<ContractEvent>,
            )>,
        >,
    ) -> Result<()> {
        let (first_version, _) = self.replay_from_version.unwrap();
        restore_handler.force_state_version_for_kv_restore(first_version.checked_sub(1))?;

        let mut base_version = first_version;
        let mut offset = 0u64;
        let replay_start = Instant::now();
        let arc_restore_handler = Arc::new(restore_handler.clone());

        let db_commit_stream = txns_to_execute_stream
            .try_chunks(BATCH_SIZE)
            .err_into::<anyhow::Error>()
            .map_ok(|chunk| {
                // A batch must not span an epoch boundary.
                stream::iter(
                    split_at_epoch_endings(chunk, |(.., events)| {
                        events.iter().any(ContractEvent::is_new_epoch_event)
                    })
                    .into_iter()
                    .map(Result::<_>::Ok),
                )
            })
            .try_flatten()
            .map_ok(|chunk| {
                let (txns, persisted_aux_info, txn_infos, write_sets, events): (
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                ) = chunk.into_iter().multiunzip();
                let handler = arc_restore_handler.clone();
                base_version += offset;
                offset = txns.len() as u64;
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
                    })
                    .err_into::<anyhow::Error>()
                    .await
                }
            })
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

**File:** types/src/transaction/mod.rs (L2693-2751)
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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L223-275)
```rust
    for (idx, txn_info) in txn_infos.iter().enumerate() {
        TransactionInfoDb::put_transaction_info(
            first_version + idx as Version,
            txn_info,
            &mut ledger_db_batch.transaction_info_db_batches,
        )?;
    }

    ledger_db
        .transaction_accumulator_db()
        .put_transaction_accumulator(
            first_version,
            txn_infos,
            &mut ledger_db_batch.transaction_accumulator_db_batches,
        )?;

    ledger_db.event_db().put_events_multiple_versions(
        first_version,
        events,
        &mut ledger_db_batch.event_db_batches,
    )?;

    for (idx, txn_events) in events.iter().enumerate() {
        for event in txn_events {
            if let Some(event_key) = event.event_key() {
                if *event_key == new_block_event_key() {
                    LedgerMetadataDb::put_block_info(
                        first_version + idx as Version,
                        event,
                        &mut ledger_db_batch.ledger_metadata_db_batches,
                    )?;
                }
            }
        }
    }
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
