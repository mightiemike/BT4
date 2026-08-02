Based on my investigation, I found a concrete, locally-supported integrity gap in the transaction-backup restore path, distinct from the external gas-refund report but analogous in class ("value accepted without being bound to the invariant that is supposed to authenticate it").

### Title
Write sets loaded from transaction backups are persisted to durable ledger storage without ever being checked against the accumulator-verified `TransactionInfo.state_change_hash` - (File: `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

### Summary
`LoadedChunk::load` reads `write_set` blobs straight out of the backup manifest file and stores them in `write_sets: Vec<WriteSet>`, completely separate from the `TransactionListWithProofV2` structure that is actually accumulator/signature-verified.

### Finding Description
In `LoadedChunk::load` [1](#0-0) , each record from the backup file is deserialized into `(txn, aux_info, txn_info, events, write_set)` and `write_set` is pushed into a plain `Vec<WriteSet>`. The verification object that is actually checked cryptographically is built and verified without `write_sets` at all: [2](#0-1) 

`TransactionInfoListWithProof::verify` (the routine underlying this call) only checks that the accumulator proof reconstructs `ledger_info.transaction_accumulator_hash()` from the hashes of the `TransactionInfo` objects themselves [3](#0-2) . `TransactionInfo` only carries `state_change_hash` (the hash of the write set), not the write set itself, so nothing in this call path recomputes `CryptoHash::hash(&write_set)` and compares it to `txn_info.state_change_hash()`.

The raw, unverified `write_sets` are then handed straight to `restore_handler.save_transactions(...)` for versions before the replay cutoff [4](#0-3) , which flows into `save_transactions_impl`, which persists them verbatim via `WriteSetDb::put_write_set` [5](#0-4) . This is durable ledger data (`WriteSetSchema`) returned later through the write-set/`TransactionInfo.changes` surface of the API [6](#0-5) .

I could not fully inspect the body of `TransactionListWithProofV2::verify`/`TransactionListWithProof::verify` in this session (only match counts were available, not source), so I cannot rule out that some other layer re-derives write-set integrity implicitly elsewhere in the replay path. What is confirmed structurally is that `write_sets` is never a field of the struct passed to `.verify(...)` in this function, so it is architecturally impossible for that specific call to validate it.

### Impact Explanation
For the "save before replay" path (which covers most of history when bootstrapping/restoring a fresh node from a full backup, since transactions before the configured `replay_from_version` are saved directly rather than re-executed through the VM/`ChunkExecutor`), a compromised or malicious backup storage backend (S3 bucket, GCS, mirror, or a MITM on the backup transport) can substitute an arbitrary `write_set` for any historical version while keeping the legitimate, accumulator/ledger-info-verified `Transaction` and `TransactionInfo`. The restoring node will accept and durably commit this forged write set, corrupting `write_set_db` and any REST API `changes`/write-set output derived from it for that version, even though the transaction hash, event root, and accumulator proof all check out as "verified." This satisfies the state-integrity gate's "committed state that differs from correct VM result / corrupts durable ledger data" and "authenticated API output bound to wrong version/object" criteria, without relying on privileged/admin assumptions (any party that can serve or tamper with the archive content can trigger it).

### Likelihood Explanation
Requires control over, or a MITM position on, the backup storage/transport used by a node performing restore/fast-bootstrap — this is a standard, commonly-used operational flow (`db-tool restore`, backup verify pipeline) rather than a privileged internal path, so it is plausible whenever backups are fetched from third-party or less-trusted storage (the very use case backups are designed for). It does not require validator keys, consensus power, or any on-chain privilege.

### Recommendation
Before persisting `write_sets` in the non-replay save path, recompute `CryptoHash::hash(&write_set)` for each transaction and assert equality with the corresponding `txn_info.state_change_hash()`, failing the chunk load (as is already done for the accumulator/ledger-info checks) if it does not match.

### Proof of Concept
Not independently executed in this session (read-only investigation). The structural argument is: (1) construct a backup transaction chunk file where the `TransactionInfo`/proof/ledger-info triple is legitimate and unmodified, but the accompanying `write_set` bytes for one record are replaced with different, still-well-formed BCS `WriteSet` bytes; (2) run `TransactionRestoreController`/`db-tool restore Oneoff Transaction` against this tampered manifest with `replay_from_version` unset or set past that version; (3) observe that `LoadedChunk::load`'s `txn_list_with_proof.verify(...)` succeeds (it never touches `write_sets`), and the tampered `write_set` is written to `write_set_db` via `save_transactions_impl`.

**Uncertainty**: I was unable to view the full source of `TransactionListWithProof`/`TransactionListWithProofV2::verify` in this session, so I cannot 100% rule out an independent write-set check elsewhere in that code (it appeared in `types/src/transaction/mod.rs` under other `fn verify` matches I did not read). This should be confirmed by reading that verify implementation directly before treating this as fully proven.

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

**File:** types/src/proof/definition.rs (L908-925)
```rust
    /// Verifies the list of transaction infos are correct using the proof. The verifier
    /// needs to have the ledger info and the version of the first transaction in possession.
    pub fn verify(
        &self,
        ledger_info: &LedgerInfo,
        first_transaction_info_version: Option<Version>,
    ) -> Result<()> {
        let txn_info_hashes: Vec<_> = self
            .transaction_infos
            .iter()
            .map(CryptoHash::hash)
            .collect();
        self.ledger_info_to_transaction_infos_proof.verify(
            ledger_info.transaction_accumulator_hash(),
            first_transaction_info_version,
            &txn_info_hashes,
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

**File:** api/types/src/transaction.rs (L365-388)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, Object)]
pub struct TransactionInfo {
    pub version: U64,
    pub hash: HashValue,
    pub state_change_hash: HashValue,
    pub event_root_hash: HashValue,
    pub state_checkpoint_hash: Option<HashValue>,
    pub gas_used: U64,
    /// Whether the transaction was successful
    pub success: bool,
    /// The VM status of the transaction, can tell useful information in a failure
    pub vm_status: String,
    pub accumulator_root_hash: HashValue,
    /// Final state of resources changed by the transaction
    pub changes: Vec<WriteSetChange>,
    /// Block height that the transaction belongs in, this field will not be present through the API
    #[oai(skip)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub block_height: Option<U64>,
    /// Epoch of the transaction belongs in, this field will not be present through the API
    #[oai(skip)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub epoch: Option<U64>,
}
```
