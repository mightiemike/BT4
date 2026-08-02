## Title
Backup restore path persists `write_sets` to the ledger DB without verifying they hash-match the accumulator-authenticated `TransactionInfo.state_change_hash` - (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`)

## Summary
During transaction-backup restore, `LoadedChunk::load` reads `(txn, aux_info, txn_info, events, write_set)` tuples straight from the backup file and only runs cryptographic verification over `txns`, `txn_infos`, and `events` via `TransactionListWithProofV2::verify`. The `write_sets` vector is populated from the same untrusted backup records but is never included in that verification, so it is never checked against the accumulator-authenticated `state_change_hash` carried inside the proven `TransactionInfo`. These unverified write sets are the ones ultimately persisted into the ledger DB.

## Finding Description
`LoadedChunk::load` builds the verification object like this: [1](#0-0) 

Note that `write_sets` (collected at line 110/136) is not passed into `TransactionListWithProof`/`TransactionListWithProofV2` at all — only `txns`, `event_vecs`, and `txn_infos` are. The verification performed by `TransactionListWithProofV2`/`TransactionListWithProof::verify` (see `types/src/transaction/mod.rs:2693-2752`) checks transaction hashes, event root hashes, and the accumulator proof of `txn_infos`, but has no write-set field to check at all. [2](#0-1) 

Compare this with the codebase's own established invariant-check pattern, `TransactionOutput::ensure_match_transaction_info`, which explicitly hashes the write set and compares it to `txn_info.state_change_hash()`: [3](#0-2) 

and the equivalent check performed in `TransactionOutputListWithProof::verify` for the state-sync path: [4](#0-3) 

No such check exists for `LoadedChunk.write_sets` in the backup/restore code. After loading, `restore_utils::save_transactions` writes the write sets directly into `WriteSetDb` and (when `kv_replay` is set) derives new state values/roots from them via `state_store.calculate_state_and_put_updates`, all without any hash-binding to the authenticated `TransactionInfo`: [5](#0-4) 

This mirrors the structural pattern of the Balancer bug: the protocol has an authenticated/bound value (Balancer's step-chained `amount`; here, `TransactionInfo.state_change_hash` proven by the accumulator) and a second, attacker/backup-storage-influenced value that is supposed to match it (Balancer's caller-supplied swap `amount`; here, the raw `write_set` bytes read from the backup chunk file) — but the code path that consumes the second value skips validating it against the first before it is committed to durable, authenticated state.

## Impact Explanation
If the transaction-backup archive (local filesystem, S3/GCS, or any other `BackupStorage` implementation) is corrupted, tampered with, or served by a compromised/malicious backup-storage backend, a restored node can end up with a `WriteSetDb` (and, in `kv_replay` mode, a rebuilt state tree / state values) that diverges from the value actually authenticated by `TransactionInfo.state_change_hash` and, transitively, from the accumulator root and the signed `LedgerInfo`. The restored node would present this incorrect state as if it were the canonical ledger state at that version, since `TransactionInfo` (which passed accumulator verification) is stored as-is while the corresponding `WriteSet` is not cross-checked. This is a state-commitment integrity break: committed/restored ledger data can differ from the true VM/ledger result while still appearing to have "passed verification," because verification only covers transactions/events/txn-info hashes, not the write set that is actually persisted.

## Likelihood Explanation
This requires an attacker (or accidental corruption) with control over, or a MITM position on, the backup storage/transport used during restore (this is explicitly out of the "malicious peer" exclusion only if it's a validator's own peer network — but backup storage is a distinct, often less-trusted channel: S3 buckets, shared file systems, third-party CDNs). Given how the code is structured, exploitation doesn't require breaking any cryptography — it only requires substituting the `write_set` bytes in a transaction-chunk record while leaving `txn_info` untouched, which the loader will accept, since it never checks the two against each other. This is plausible for a full-node/backup restore operator pulling from a network-hosted or third-party backup store.

## Recommendation
In `LoadedChunk::load` (or immediately after, before any use of `write_sets`), for every transaction verify `CryptoHash::hash(&write_set) == txn_info.state_change_hash()`, exactly as `TransactionOutput::ensure_match_transaction_info` and `TransactionOutputListWithProof::verify` already do elsewhere in the codebase. This check should be added prior to constructing `Self { ..., write_sets }` so that a chunk failing the check is rejected before any data is persisted via `restore_utils::save_transactions`.

## Proof of Concept
Not independently reproduced in this review (no test harness execution was performed). Conceptually:
1. Produce a valid transaction backup chunk (`manifest.transactions` file) for some version range using the normal backup path.
2. Replace the serialized `WriteSet` bytes of one record with an arbitrary different write set (leaving `Transaction`, `TransactionInfo`, `PersistedAuxiliaryInfo`, and events untouched, so `txn_info.state_change_hash()` still matches the original, correct write set — not the substituted one).
3. Point `TransactionRestoreController`/`TransactionRestoreBatchController` at this tampered manifest.
4. Observe that `LoadedChunk::load` succeeds (its `verify()` call has no dependency on `write_sets`), and the tampered write set is written to `WriteSetDb` (and, if `kv_replay` is enabled, folded into `state_store.calculate_state_and_put_updates`), producing a restored ledger state that diverges from the state authenticated by the original chain's accumulator-proven `TransactionInfo`.

Note: I was not able to trace, in the time available, whether a downstream step (e.g., epoch-ending verification, `VerifyExecutionMode`, or a later re-execution pass in the full `RestoreCoordinator` flow) independently re-derives and cross-checks the write set hash before the DB write is considered final; if such a check exists elsewhere in the pipeline it would mitigate this finding, and I could not fully rule that out given remaining budget. This should be confirmed directly in a Devin session with full repo/test access.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L105-137)
```rust
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
