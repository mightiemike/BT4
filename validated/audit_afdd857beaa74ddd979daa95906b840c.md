## Title
Unverified `LedgerInfo` accepted during transaction-backup restore when epoch is "too new" for known epoch history — `EpochHistory::verify_ledger_info()` (File: `storage/backup/backup-cli/src/backup_types/epoch_ending/restore.rs`)

### Summary
`EpochHistory::verify_ledger_info()` is the function responsible for authenticating the `LedgerInfoWithSignatures` that accompanies every transaction-chunk range proof during backup restore. When the ledger info's `epoch` exceeds the number of already-verified epoch-ending records held by the caller, the function silently returns `Ok(())` instead of failing, effectively skipping signature/validator-set verification for that ledger info.

### Finding Description
`EpochHistory::verify_ledger_info` is defined as: [1](#0-0) 

The critical branch is:
```rust
if epoch > self.epoch_endings.len() as u64 {
    // TODO(aldenhu): fix this from upper level
    warn!(...);
    return Ok(());
}
```
which returns success without checking the ledger info against any known validator set whenever `epoch` is larger than the size of the accumulated, already-verified `epoch_endings` vector.

This function is the *only* authentication step applied to the `LedgerInfoWithSignatures` used when restoring a transaction chunk: [2](#0-1) 

After `epoch_history.verify_ledger_info(&ledger_info)?` "succeeds" (even via the silent skip), the code calls `txn_list_with_proof.verify(ledger_info.ledger_info(), ...)`, which only checks that the supplied transactions/`TransactionInfo`s hash into the accumulator root **claimed by that same, unauthenticated `ledger_info`**. If the ledger info itself was never checked against a real validator signature set, an attacker who controls the backup storage (the input to this whole pipeline, not a privileged component) can supply:
- an arbitrary `LedgerInfo` with an `epoch` field set high enough to exceed `epoch_endings.len()`,
- an arbitrary accumulator root/version inside that `LedgerInfo`,
- and any set of transactions/`TransactionInfo`s/write-sets that hash correctly against that self-chosen root,

and the restore path will accept and persist them via `restore_utils::save_transactions`, corrupting the ledger's committed transaction history, events, and write sets for that version range with attacker-chosen data, while still reporting a "verified" restore.

### Impact Explanation
This breaks the core state-commitment/proof-integrity invariant that authenticated ledger info must be bound to the actual validator-signed root before it is used to validate any proof or committed data. A malicious or compromised backup source can inject transactions/write-sets that were never executed by the real validator set into a node's restored ledger, producing durable, silently-accepted state divergence from the correct VM/consensus result. This matches the "Committed state that differs from the correct VM result or corrupts durable ledger data" and "wrong ... transaction proof ... accepted as valid" impact classes.

### Likelihood Explanation
The bypass condition (`epoch > epoch_endings.len()`) is reachable whenever `EpochHistory` does not already contain a verified epoch-ending record for the chunk's claimed epoch — e.g., a chunk belonging to an epoch that has not yet ended (no epoch-ending `LedgerInfo` exists for it) or when a restore/verification tool is fed an incomplete epoch history. Because the epoch value being checked is a field of the very `ledger_info` that has not yet been validated, an attacker fully controls whether this branch triggers. The code's own `// TODO(aldenhu): fix this from upper level` comment indicates the authors are aware this check is incomplete for at least some legitimate restore flows, making the gap self-acknowledged and realistically reachable in production restore/verify operations, not merely a hypothetical edge case.

### Recommendation
Do not return `Ok(())` when `epoch > epoch_endings.len()`. Instead, either (a) fail the restore/verification with an error requiring the caller to supply a complete epoch-ending history up to the target version before any transaction chunk in that epoch is processed, or (b) defer the transaction chunk's finalization until the corresponding epoch-ending `LedgerInfo` has been verified, ensuring no unauthenticated `LedgerInfo` is ever used to validate a transaction accumulator range proof.

### Proof of Concept
1. Prepare a malicious transaction backup manifest whose chunk's proof file contains a `LedgerInfoWithSignatures` with: `epoch = N` (some large value guaranteed to exceed the restorer's current `epoch_endings.len()`), an accumulator root hash `R`, and empty/forged aggregate signatures.
2. Craft a set of transactions/`TransactionInfo`s whose `TransactionAccumulatorRangeProof` hashes correctly to `R` (trivial since the attacker controls both sides).
3. Run `TransactionRestoreController`/`LoadedChunk::load` against this manifest with an `EpochHistory` that has fewer than `N` recorded epoch endings (e.g., a partial restore, or restoring the still-open final epoch of the chain).
4. Observe `epoch_history.verify_ledger_info(&ledger_info)` returns `Ok(())` via the `epoch > self.epoch_endings.len()` branch (only a `warn!` log is emitted), then `txn_list_with_proof.verify()` succeeds because the proof is self-consistent with the attacker-chosen root, and the forged transactions/write-sets are written to the database via `restore_utils::save_transactions`. [3](#0-2) [4](#0-3)

### Citations

**File:** storage/backup/backup-cli/src/backup_types/epoch_ending/restore.rs (L276-312)
```rust
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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L147-167)
```rust
        let (range_proof, ledger_info) = storage
            .load_bcs_file::<(TransactionAccumulatorRangeProof, LedgerInfoWithSignatures)>(
                &manifest.proof,
            )
            .await?;
        if let Some(epoch_history) = epoch_history {
            epoch_history.verify_ledger_info(&ledger_info)?;
        }

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
