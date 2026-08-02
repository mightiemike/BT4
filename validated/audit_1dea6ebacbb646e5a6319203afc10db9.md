### Title
`ensure_match_transaction_info()` skips state-checkpoint-hash comparison, letting replay-verify accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the authenticated-consistency check used by replay/verify tooling to compare a freshly re-executed `TransactionOutput` against the archived, proof-authenticated `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but explicitly **does not** check `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the value carried by the trusted `TransactionInfo`. This mirrors the external bug-class pattern of "a function that is expected to validate/return a real computed value instead validates/returns something else," silently letting a wrong state value pass as correct.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info()` is documented to ensure a `TransactionOutput` matches a `TransactionInfo` (which in the replay tool comes from a backup that itself was verified against a signed `LedgerInfo`/accumulator proof, see `TransactionInfoWithProof::verify` / `TransactionInfoListWithProof::verify`): [2](#0-1) 

The function computes and compares `write_set_hash` against `txn_info.state_change_hash()` and `event_root_hash` against `txn_info.event_root_hash()`, but it stops there — the trailing comment is a self-documented admission of the gap: [3](#0-2) 

The `state_checkpoint_hash` (and the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields added for the trading-native state trees) are never derived from the locally re-executed state and compared to the value embedded in the trusted `TransactionInfo`. This means the invariant "committed/replayed state must match the accumulator-proof-authenticated `TransactionInfo`" is only partially enforced: write-set contents and events are checked, but the Merkle root that actually commits to the *entire post-execution state* is not.

This function is used directly by the replay-verify tool `storage/db-tool/src/replay_on_archive.rs`: [4](#0-3) 

Here, `expected_txn_infos[idx]` is loaded from backup and is proof-authenticated data (bound to a real `LedgerInfo`/accumulator root), while `executed_outputs[idx]` is the locally re-computed VM output. The call to `ensure_match_transaction_info` is the *only* place these two are cross-checked before the tool declares the version "verified." Because the state-checkpoint hash is skipped, a state root computed locally that diverges from the authenticated ledger (e.g., due to a JMT/SMT computation bug, a storage schema bug, or corrupted/incorrectly restored state) will not be flagged as a mismatch by this tool, even though write set entries and events line up.

### Impact Explanation
This breaks the "Proof And Storage Pivots" invariant that "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged" and that "replay paths… must not reinterpret committed data into a different ledger state." A hard-fork-only state divergence (the state root differs while write-set op list and events match, which can legitimately happen since `state_checkpoint_hash` commits to the full resulting state including all prior versions and native/position/hot-state trees, not just this transaction's write-set) would go undetected by `replay_on_archive`'s verify-only path, producing false confidence that a replayed/restored chain segment is state-correct. That is exactly the "Hard-fork-only divergence during commit, replay, restore, or proof verification" category called out as in-scope.

### Likelihood Explanation
This is a genuine, currently-present code path (not hypothetical): the check is real production tooling (`db-tool replay-on-archive`) used for auditing/restoring archived nodes, and the gap is acknowledged by the in-repo TODO comment itself, which states the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) should not be enabled until this comparator is fixed. However, I could not fully verify from the indexed code whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently disabled by default across all deployments, or whether some other layer (e.g., a separate JMT proof check elsewhere in the restore/verify pipeline, outside what I could inspect) already independently re-validates the state checkpoint hash for the non-trading-native case. The `state_checkpoint_hash` field predates the trading-native feature and is populated on essentially every checkpoint transaction, so the gap plausibly affects standard state-checkpoint verification during `replay_on_archive`, not just the new trading-native trees — but I was not able to trace every alternate validation path (e.g., within `aptos_debugger.rs` or `cli/src/commands.rs`, which also call this function) in the remaining budget, so likelihood is stated with that caveat.

### Recommendation
Extend `ensure_match_transaction_info()` to also recompute the state-checkpoint hash (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) from the replayed state and assert equality with `txn_info.state_checkpoint_hash()` (and the other checkpoint hash accessors), the same way `write_set_hash` and `event_root_hash` are already checked, before gating `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (and ideally regardless of that flag, for the base `state_checkpoint_hash`).

### Proof of Concept
Not independently reproducible as a runnable exploit from static review alone (would require driving `replay_on_archive` against a DB/backup pair engineered to have a matching write-set/events but a diverging computed state checkpoint hash — e.g., via a corrupted local state snapshot or a JMT computation regression). The structural PoC is the code path itself:
1. `LoadedChunk`/backup verify path authenticates `expected_txn_info.state_checkpoint_hash()` via `TransactionInfoListWithProof::verify` against a signed `LedgerInfo` [5](#0-4) .
2. `replay_on_archive::Verifier::execute_and_verify` re-executes transactions locally and calls `ensure_match_transaction_info` [6](#0-5) .
3. `ensure_match_transaction_info` never compares the locally derived state checkpoint hash to `expected_txn_info.state_checkpoint_hash()` [7](#0-6) , so a run with a corrupted/incorrect state root but matching write-set/events would pass verification silently.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2204)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
        const ERR_MSG: &str = "TransactionOutput does not match TransactionInfo";

        let expected_txn_status: TransactionStatus = txn_info.status().clone().into();
        ensure!(
            self.status() == &expected_txn_status,
            "{}: version:{}, status:{:?}, auxiliary data:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.status(),
            self.auxiliary_data(),
            expected_txn_status,
        );

        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );

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

        let event_hashes = self
            .events()
            .iter()
            .map(CryptoHash::hash)
            .collect::<Vec<_>>();
        let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
        ensure!(
            event_root_hash == txn_info.event_root_hash(),
            "{}: version:{}, event_root_hash:{:?}, expected:{:?}, events: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            event_root_hash,
            txn_info.event_root_hash(),
            self.events(),
            expected_events,
        );

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** types/src/proof/definition.rs (L864-875)
```rust
    /// Verifies that the `TransactionInfo` exists in the ledger represented by the `LedgerInfo`
    /// at specified version.
    pub fn verify(&self, ledger_info: &LedgerInfo, transaction_version: Version) -> Result<()> {
        verify_transaction_info(
            ledger_info,
            transaction_version,
            &self.transaction_info,
            &self.ledger_info_to_transaction_info_proof,
        )?;
        Ok(())
    }
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
        }
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L147-174)
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
        // and disassemble it to get things back.
        let (txn_list_with_proof, persisted_aux_info) = txn_list_with_proof.into_parts();
        let txns = txn_list_with_proof.transactions;
        let range_proof = txn_list_with_proof
            .proof
            .ledger_info_to_transaction_infos_proof;
        let txn_infos = txn_list_with_proof.proof.transaction_infos;
```
