Based on my investigation, I found a genuine analog to the "missing two-step ownership handoff" bug class in the backup/restore proof-verification code, in `EpochHistory::verify_ledger_info`.

### Title
Backup `EpochHistory::verify_ledger_info` silently skips signature verification for "too new" epochs, allowing an untrusted `LedgerInfo` to be accepted during restore - (File: `storage/backup/backup-cli/src/backup_types/epoch_ending/restore.rs`)

### Summary
Similar to the reported bug where a critical authorization/verification step (`nominateNewDependencyOwner`) is silently skipped during a state-transition (deployment controller switch), the Aptos backup-restore code has an analogous silent-skip: `EpochHistory::verify_ledger_info` is supposed to cryptographically verify every `LedgerInfoWithSignatures` encountered during restore against the chain of trust built from `epoch_endings`, but for any epoch number greater than what's currently known, it skips verification entirely and returns `Ok(())` with only a warning log.

### Finding Description
`EpochHistory::verify_ledger_info` is the function relied upon by both `transaction/restore.rs` and `state_snapshot/restore.rs` to bind loaded transaction/state chunks to an authenticated `LedgerInfo`: [1](#0-0) 

Specifically, the branch:
```rust
if epoch > self.epoch_endings.len() as u64 {
    // TODO(aldenhu): fix this from upper level
    warn!(...);
    return Ok(());
}
```
returns success without checking the `LedgerInfoWithSignatures` against any validator set/epoch state, whenever the epoch claimed by the supplied ledger info exceeds what has already been chained/verified in `epoch_endings`. This is called directly from the transaction-chunk loader: [2](#0-1) 

and from the state-snapshot restore path: [3](#0-2) 

In both call sites, once `epoch_history.verify_ledger_info(&li)` (or `&ledger_info`) returns `Ok(())`, the code proceeds to treat the accompanying `LedgerInfo` as trusted and uses it to validate `TransactionListWithProofV2` / `TransactionInfoWithProof` / root-hash bindings — i.e., the entire chunk's transactions, write sets, and state root hash are accepted as authentic based on a `LedgerInfo` whose signatures were never actually checked.

### Impact Explanation
This breaks the accumulator/Merkle-proof-binding invariant required by the "Proof And Storage Pivots" gate: a proof-bearing response (the backup chunk + its `LedgerInfoWithSignatures`) is accepted and used to seed durable ledger data (`restore_handler.save_transactions`, state snapshot commit) without being cryptographically bound to the correct validator-signed root. If the backup source (e.g., an S3 bucket or other storage backend used as the`BackupStorage` implementation) is compromised or supplies a forged manifest claiming an epoch newer than the restorer's currently-known epoch history, and no `trusted_waypoints` entry covers that specific version, the restore tool will commit attacker-supplied write sets/transaction infos/state values into the database as if verified. This is a hard-fork-class divergence: an operator restoring a node from that backup ends up with ledger state/version-bound data that differs from the network's actual committed state, corrupting durable ledger data without any error being surfaced (only a `warn!` log).

### Likelihood Explanation
This bypass triggers on a normal, easily reachable condition — any epoch beyond `epoch_endings.len()` — not on a rare edge case, and the code path is exercised on every restore of transaction and state-snapshot chunks. Exploitability depends on control over the backup source/manifest content, which is plausible in supply-chain or man-in-the-middle scenarios against the backup storage. The comment `// TODO(aldenhu): fix this from upper level` confirms the developers are aware verification is incomplete here, and the accompanying prose "Epoch is too new and can't be verified... node won't be able to start if this data is malicious" is an unsubstantiated assumption — nothing in `verify_ledger_info`, nor in the direct callers shown above, actually re-verifies this later.

### Recommendation
`EpochHistory::verify_ledger_info` should not return `Ok(())` when the epoch is beyond what's covered by `epoch_endings`/`trusted_waypoints`. Instead, it should either (a) require callers to supply a chained/consecutive epoch history that extends far enough to cover the ledger info in question before proceeding, or (b) explicitly propagate an error/require an external waypoint check before treating such a `LedgerInfo` as trusted, closing the gap noted in the existing TODO.

### Proof of Concept
1. Prepare a malicious `transaction` backup with a `TransactionChunk` whose accompanying `(TransactionAccumulatorRangeProof, LedgerInfoWithSignatures)` file contains a forged `LedgerInfo` claiming `epoch = N` where `N > epoch_history.epoch_endings.len()`, and no `trusted_waypoints` entry exists for that version.
2. Run the restore tool pointing at this backup; `TransactionRestoreController` invokes `LoadedChunk::load`, which calls `epoch_history.verify_ledger_info(&ledger_info)`.
3. `verify_ledger_info` hits `epoch > self.epoch_endings.len()`, logs a warning, and returns `Ok(())` — no signature check performed.
4. Restore proceeds to call `txn_list_with_proof.verify(ledger_info.ledger_info(), ...)`, which only checks internal consistency of the proof against the (forged, unverified) `LedgerInfo`'s root, not that the `LedgerInfo` itself is authentic.
5. Attacker-controlled write sets/transactions are committed to durable storage via `restore_handler.save_transactions`, corrupting the resulting ledger state.

### Citations

**File:** storage/backup/backup-cli/src/backup_types/epoch_ending/restore.rs (L275-312)
```rust
impl EpochHistory {
    pub fn verify_ledger_info(&self, li_with_sigs: &LedgerInfoWithSignatures) -> Result<()> {
        let epoch = li_with_sigs.ledger_info().epoch();
        ensure!(!self.epoch_endings.is_empty(), "Empty epoch history.",);
        if epoch > self.epoch_endings.len() as u64 {
            // TODO(aldenhu): fix this from upper level
            warn!(
                epoch = epoch,
                epoch_history_until = self.epoch_endings.len(),
                "Epoch is too new and can't be verified. Previous chunks are verified and node \
                won't be able to start if this data is malicious."
            );
            return Ok(());
        }
        if epoch == 0 {
            ensure!(
                li_with_sigs.ledger_info() == &self.epoch_endings[0],
                "Genesis epoch LedgerInfo info doesn't match.",
            );
        } else if let Some(wp_trusted) = self
            .trusted_waypoints
            .get(&li_with_sigs.ledger_info().version())
        {
            let wp_li = Waypoint::new_any(li_with_sigs.ledger_info());
            ensure!(
                *wp_trusted == wp_li,
                "Waypoints don't match. In backup: {}, trusted: {}",
                wp_li,
                wp_trusted,
            );
        } else {
            self.epoch_endings[epoch as usize - 1]
                .next_epoch_state()
                .ok_or_else(|| anyhow!("Shouldn't contain non- epoch bumping LIs."))?
                .verify(li_with_sigs)?;
        };
        Ok(())
    }
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L147-154)
```rust
        let (range_proof, ledger_info) = storage
            .load_bcs_file::<(TransactionAccumulatorRangeProof, LedgerInfoWithSignatures)>(
                &manifest.proof,
            )
            .await?;
        if let Some(epoch_history) = epoch_history {
            epoch_history.verify_ledger_info(&ledger_info)?;
        }
```

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L123-139)
```rust
        let manifest: StateSnapshotBackup =
            self.storage.load_json_file(&self.manifest_handle).await?;
        let (txn_info_with_proof, li): (TransactionInfoWithProof, LedgerInfoWithSignatures) =
            self.storage.load_bcs_file(&manifest.proof).await?;
        txn_info_with_proof.verify(li.ledger_info(), manifest.version)?;
        let state_root_hash = txn_info_with_proof
            .transaction_info()
            .ensure_state_checkpoint_hash()?;
        ensure!(
            state_root_hash == manifest.root_hash,
            "Root hash mismatch with that in proof. root hash: {}, expected: {}",
            manifest.root_hash,
            state_root_hash,
        );
        if let Some(epoch_history) = self.epoch_history.as_ref() {
            epoch_history.verify_ledger_info(&li)?;
        }
```
