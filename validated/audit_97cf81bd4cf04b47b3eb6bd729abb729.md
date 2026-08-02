## Title
Backup restore accepts and persists transaction write sets that are never authenticated against the accumulator-proven `TransactionInfo.state_change_hash` - ([File: storage/backup/backup-cli/src/backup_types/transaction/restore.rs])

### Summary
The transaction-backup restore path reads `(txn, aux_info, txn_info, events, write_set)` tuples from backup file records and verifies the chunk using `TransactionListWithProofV2::verify`, but that verification type/method never includes or checks `write_set` at all. The `write_sets` vector is carried through `LoadedChunk` untouched and is later persisted directly to the ledger DB (`WriteSetDb`/state KV store) via `restore_utils::save_transactions_impl`, without ever confirming `CryptoHash::hash(write_set) == txn_info.state_change_hash()`. This differs from the output-based restore path (`TransactionOutputListWithProofV2::verify`), which explicitly performs that check.

### Finding Description
`LoadedChunk::load` in [1](#0-0)  deserializes `write_set` directly from backup record bytes into a `write_sets: Vec<WriteSet>` alongside `txns`, `txn_infos`, and `event_vecs`.

The code then builds a `TransactionListWithProofV2` from `txns`, `event_vecs`, and `txn_infos` (deliberately **excluding** `write_sets`) and calls `.verify(...)`: [2](#0-1) 

`TransactionListWithProof::verify` only checks (a) transaction hash vs `txn_info.transaction_hash()`, (b) events vs `txn_info.event_root_hash()`, and (c) the accumulator proof vs the ledger info: [3](#0-2) 

There is no field or method in this type/verification path that touches `state_change_hash` or a write set at all. Compare this with the sibling type `TransactionOutputListWithProofV2::verify`, which explicitly authenticates the write set: [4](#0-3) 

After `verify()` "succeeds", `LoadedChunk::load` simply reattaches the never-validated `write_sets` to the result: [5](#0-4) 

These `write_sets` are then propagated to the two persistence paths:
- Non-replay direct save via `save_transactions_and_replay_kv` / `save_transactions`, which calls `restore_utils::save_transactions_impl`, whose signature takes `write_sets: &[WriteSet]` and stores them into `WriteSetDb` / state KV batches: [6](#0-5) 
- Replay-based paths (`replay_kv`, `replay_transactions`), which stream `write_sets` alongside `txns`/`txn_infos` into `save_transactions_and_replay_kv` / `chunk_replayer.enqueue_chunks`: [7](#0-6) [8](#0-7) 

In the KV-only replay path (`replay_kv`), the write sets are written straight to durable storage with `handler.save_transactions_and_replay_kv(...)` — there is no re-execution and no independent state_change_hash check performed anywhere in the reviewed code before that call.

The `TransactionInfo.state_change_hash` field is precisely the field designed to bind a write set to the accumulator-authenticated transaction info — this is exactly the invariant enforced elsewhere (e.g. `TransactionOutput::ensure_match_transaction_info` at [9](#0-8) , and `TransactionOutputListWithProofV2::verify`). The transaction-backup restore code path constructs the wrong verification object (`TransactionListWithProofV2`, which structurally has no write-set slot) instead of one that authenticates the write set, so this binding is silently skipped for the transaction-chunk-format restore/replay path.

### Impact Explanation
If an attacker (or corruption) can supply a backup archive/chunk with a substituted `write_set` for a given `(txn, txn_info)` pair while leaving `txn`, `txn_info`, and the accumulator proof intact and valid, `LoadedChunk::load` will accept the chunk as "verified," and the forged write set will be persisted to `WriteSetDb`/state K-V store as if it were the authentic, accumulator-committed result of that transaction. This corrupts durable ledger state (state values readable by later `get_state_value_by_version`, replay, and the `write_set` schema at `storage/aptosdb/src/schema/write_set/mod.rs`) while the accumulator-proven `TransactionInfo.state_change_hash` on disk no longer matches the actually-stored write set. This is a direct "committed state that differs from the correct VM result / corrupts durable ledger data" impact per the state-integrity gate, reachable through a code path (backup restore) explicitly named in scope ("restore flows").

### Likelihood Explanation
Requires the restore operator/tool to consume an untrusted or tampered backup source (e.g. a compromised or malicious backup storage backend/CDN, or a MITM'd chunk download) — restore tooling generally trusts the backup storage integrity via the ledger-info signature and accumulator proof alone, which is exactly the guarantee this bug breaks for write sets. Given backups are frequently fetched over the network from S3/GCS or similar shared storage, and this is one of the areas the task explicitly calls out ("restore flows"), the likelihood of a compromised/malicious backup source reaching this code is realistic, though it does require control over the backup artifact rather than the live consensus/execution path.

### Recommendation
In `LoadedChunk::load`, before accepting `write_sets`, verify for every `(write_set, txn_info)` pair that `CryptoHash::hash(&write_set) == txn_info.state_change_hash()` (mirroring the check already present in `TransactionOutputListWithProofV2::verify`), or restructure the verification to construct a proof object that includes write sets so the existing `verify()` machinery enforces it uniformly. Apply the same check defensively in `restore_utils::save_transactions_impl` right before persisting, so persistence itself cannot occur with an unauthenticated write set regardless of caller.

### Proof of Concept
Conceptual (cannot execute against a live node from this analysis environment):
1. Take a legitimate transaction backup chunk file (`TransactionChunkFormat::V0` or `V1`) containing valid `(txn, txn_info, events, write_set)` records plus its accompanying `(range_proof, ledger_info)` proof file.
2. For one record, replace `write_set` bytes with an arbitrary attacker-chosen `WriteSet` (e.g., one that credits an attacker-controlled account or corrupts totals), leaving `txn`, `txn_info`, `events`, and the proof file untouched.
3. Run `LoadedChunk::load` (via the restore CLI) against this modified chunk.
4. Observe that `txn_list_with_proof.verify(...)` at [10](#0-9)  succeeds because it never inspects `write_set`, and the forged `write_set` is returned in `LoadedChunk` and subsequently persisted by `save_transactions_impl`/`save_transactions_and_replay_kv` into the restored `WriteSetDb` and state KV store, producing a durable state divergent from the honest chain history while `txn_info.state_change_hash` on disk still reflects the original (different) write set.

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L169-186)
```rust
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L553-617)
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
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L650-698)
```rust
    // replay the txn to the DB
    async fn replay_transactions(
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
        restore_handler.reset_state_store();
        let replay_start = Instant::now();
        let db = DbReaderWriter::from_arc(Arc::clone(&restore_handler.aptosdb));
        let chunk_replayer = Arc::new(ChunkExecutor::<AptosVMBlockExecutor>::new(db));
        let ledger_update_stream = txns_to_execute_stream
            .try_chunks(BATCH_SIZE)
            .err_into::<anyhow::Error>()
            .map_ok(|chunk| {
                let (txns, persisted_aux_info, txn_infos, write_sets, events): (
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                ) = chunk.into_iter().multiunzip();
                let chunk_replayer = chunk_replayer.clone();
                let verify_execution_mode = self.verify_execution_mode.clone();

                async move {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["enqueue_chunks"]);

                    tokio::task::spawn_blocking(move || {
                        chunk_replayer.enqueue_chunks(
                            txns,
                            persisted_aux_info,
                            txn_infos,
                            write_sets,
                            events,
                            &verify_execution_mode,
                        )
                    })
                    .await
                    .expect("spawn_blocking failed")
                }
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

**File:** storage/aptosdb/src/backup/restore_utils.rs (L192-206)
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
```
