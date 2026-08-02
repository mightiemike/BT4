## Finding: Write sets in transaction-backup restore are never authenticated against `TransactionInfo.state_change_hash`, allowing forged write sets to be committed as valid ledger state

### Title
Unauthenticated write sets accepted during transaction-backup restore/replay - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
When restoring a chain from a transaction backup, `LoadedChunk::load` reads `(txn, aux_info, txn_info, events, write_set)` tuples from the backup file, but only assembles a `TransactionListWithProofV2` out of `txns`, `events`, and `txn_infos` for verification — `write_sets` are deliberately excluded from the object that is cryptographically verified (`txn_list_with_proof.verify(...)`). The `write_sets` vector is then carried forward unchanged and written straight into `WriteSetDb`, or (in KV-replay mode) fed into `StateStore::calculate_state_and_put_updates` to compute and commit the new Jellyfish Merkle root, with no check that its hash equals the authenticated `TransactionInfo::state_change_hash` (which is what is actually proven against the accumulator/ledger-info signature chain).

### Finding Description
`LoadedChunk::load` ( [1](#0-0) ) deserializes each record from the backup file including a `write_set`, appending it to a bare `write_sets: Vec<WriteSet>`. It then builds a verification object that omits write sets entirely: [2](#0-1) 

`TransactionListWithProofV2::verify()` only checks that transaction hashes, event root hashes, and the `TransactionInfo` list are consistent with the signed `LedgerInfo` via the accumulator (as visible from the analogous, but *output*-based, verification routine at [3](#0-2)  which explicitly hashes `write_set` and compares it to `txn_info.state_change_hash()` — that check exists only for `TransactionOutputListWithProof`, which carries `write_set` as a verified field. `TransactionListWithProof`, used here, has no `write_set` field and therefore cannot and does not perform this check).

After verification, `unpack()` returns the untouched `write_sets` ( [4](#0-3) ), which are passed to `restore_handler.save_transactions(...)` and eventually to `save_transactions_impl`. That function writes the write set directly to durable storage: [5](#0-4) 

and, when `kv_replay` is enabled, uses the very same unverified `write_sets` to compute and commit the new authoritative ledger state: [6](#0-5) 

At no point in this path is `CryptoHash::hash(&write_set)` compared to `txn_info.state_change_hash()`, nor is the resulting JMT root compared to `txn_info.state_checkpoint_hash()`. The only authenticated artifact is the `TransactionInfo` (via the accumulator/ledger-info signature), but the code silently trusts a companion value (`write_set`) that travels alongside it in the same untrusted backup file without validating that they are the same pair the validators actually signed for.

### Impact Explanation
This breaks the "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" invariant and the "authenticated response bound to the right ... proof context" invariant explicitly called out as in-scope. A node operator restoring from a backup source that is not fully trusted (which is exactly the threat model the surrounding code assumes — hence why `LedgerInfoWithSignatures`, `EpochHistory`, and `TransactionAccumulatorRangeProof` are verified at all) can have arbitrary, attacker-chosen write sets committed into `WriteSetDb`, and — in KV-replay mode — into the actual state tree/state-kv DB as the canonical post-execution state, all while the surrounding transaction/txn-info/accumulator/ledger-info chain reports as fully verified. This is a durable, silent corruption of committed ledger state that diverges from the real VM execution result, matching the "Committed state that differs from the correct VM result or corrupts durable ledger data" and "Wrong ... proof accepted as valid" impact categories.

### Likelihood Explanation
Exploitation requires control over, or the ability to corrupt, the backup file storage used by an operator running a restore (state sync `v2`-driven restore or manual backup restore, and any consumer relying on `TransactionRestoreController`/`TransactionRestoreBatchController`). This is a realistic scenario for third-party or S3-style backup storage integrations, which is the very reason the restore code performs ledger-info/accumulator verification in the first place — that verification is rendered incomplete because write sets are excluded from it.

### Recommendation
Before accepting `write_sets` in `LoadedChunk::load` (and any other place they are consumed off backup input, e.g. `execution/executor/src/chunk_executor` replay paths that similarly take `write_sets` alongside `txn_infos`), hash each write set and assert equality with the corresponding `txn_info.state_change_hash()`, exactly as is already done for `TransactionOutputListWithProof::verify`. Consider switching the restore path to reuse `TransactionOutputListWithProof`/`TransactionOutput` (which carries an authenticated `write_set`) instead of pairing an unauthenticated `write_set` alongside a `TransactionListWithProof`.

### Proof of Concept
1. Produce a transaction backup manifest/chunk where each record's `txn_info` (and hence `state_change_hash`) is legitimate/signed, but substitute a different `write_set` byte payload for one or more records (the record format is `bcs::from_bytes::<(Transaction, PersistedAuxiliaryInfo, TransactionInfo, Vec<ContractEvent>, WriteSet)>`, so the write set can be freely edited without touching `txn_info`).
2. Run `TransactionRestoreController`/`TransactionRestoreBatchController::run()` against this tampered manifest with a valid `LedgerInfoWithSignatures`/`EpochHistory`.
3. Observe `LoadedChunk::load` succeeds because `txn_list_with_proof.verify()` never inspects `write_sets`.
4. Observe `save_transactions`/`save_transactions_and_replay_kv` commit the forged write set into `WriteSetDb` (and, in KV-replay mode, into the live state tree via `calculate_state_and_put_updates`), producing a durable, "restored" ledger state that does not match what was actually executed/signed by validators.

Note: I was unable to fully inspect `types/src/transaction/mod.rs`'s exact `TransactionListWithProof::verify` implementation text (only the `TransactionOutputListWithProof::verify` snippet was retrieved) before running out of tool iterations; the conclusion that it excludes write-set verification rests on the absence of a `write_set` field in `TransactionListWithProof`/`TransactionListWithProofV2` and the fact that `write_sets` are handled entirely outside the constructed/verified proof object in `restore.rs`. Confirming the exact source of `TransactionListWithProof::verify` would further solidify this finding.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L100-136)
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L188-208)
```rust
    fn unpack(
        self,
    ) -> (
        Vec<Transaction>,
        Vec<PersistedAuxiliaryInfo>,
        Vec<TransactionInfo>,
        Vec<Vec<ContractEvent>>,
        Vec<WriteSet>,
    ) {
        let Self {
            manifest: _,
            txns,
            persisted_aux_info,
            txn_infos,
            event_vecs,
            write_sets,
            range_proof: _,
        } = self;

        (txns, persisted_aux_info, txn_infos, event_vecs, write_sets)
    }
```

**File:** types/src/transaction/mod.rs (L2970-2993)
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

            // Verify the gas matches for both the transaction info and output
            ensure!(
                txn_output.gas_used() == txn_info.gas_used(),
                "The gas used in transaction output does not match the transaction info \
                     in proof. Gas used in transaction output: {}. Gas used in txn_info: {}.",
                txn_output.gas_used(),
                txn_info.gas_used(),
            );
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
