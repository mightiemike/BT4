## Title
Replay-verify's transaction-output/`TransactionInfo` comparator never checks state, hot-state, or position-state checkpoint roots, letting silently-diverged committed state pass authenticated replay verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` — the single comparator used by every replay/verification tool (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger`, `aptos-move/cli`) to confirm that locally re-executed VM output matches the accumulator-authenticated `TransactionInfo` pulled from a signed backup — only checks `status`, `gas_used`, `write_set` hash, and `event_root_hash`. It never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the freshly computed values, even though these fields are the actual consensus-committed roots of the global state tree. This mirrors the reported bug-class: a check is performed against a superficially plausible but not-actually-authoritative signal (per-tx write set) instead of the value that really represents the durable, committed asset/state (the merklized state root), so a state divergence is silently accepted as correct.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0) . It validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

It explicitly does **not** check `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` — a gap the code itself calls out: [2](#0-1) .

This comparator is the sole verification oracle in `storage/db-tool/src/replay_on_archive.rs`, which re-executes a full block via `AptosVMBlockExecutor::execute_block` against archived transactions and then calls it directly on the *fully re-executed* `TransactionOutput` against the *authenticated* `expected_txn_infos` pulled from backup: [3](#0-2) . The archived `TransactionInfo` values used here are authenticated — they arrive bound to a `TransactionAccumulatorRangeProof` verified against a validator-signed `LedgerInfoWithSignatures` in the backup/restore pipeline (`LoadedChunk::load`) via `txn_list_with_proof.verify(...)`, per [4](#0-3) . So `expected_txn_infos[idx].state_checkpoint_hash()` (and hot/position roots) are genuinely the mainnet-committed state roots, yet `execute_and_verify` never compares them to the roots produced by local execution.

The write-set hash (`state_change_hash`) only proves that the transaction's own emitted write ops are byte-identical; it says nothing about whether those writes, once applied on top of the correct pre-state, produce the correct global Jellyfish Merkle Tree root. State-root divergence bugs (e.g., a JMT update bug, wrong parent-state selection, wrong node key hashing, an execution-order bug that mutates state correctly for the single txn but corrupts the broader tree) are exactly the class of consensus-safety bugs that `state_checkpoint_hash` comparison is designed to catch, and this comparator cannot catch them at all.

### Impact Explanation
`replay_on_archive` (and the CLI `commands.rs` / `aptos-debugger` callers of the same function) is the tool operators and the Aptos team use to audit that re-execution of mainnet history against a specific VM/runtime build reproduces the exact committed ledger state — this is precisely the "replay" and "authenticated response" integrity pivot called out in scope. Because the comparator omits the state/hot-state/position checkpoint hash checks, a state-root divergence introduced by a storage, checkpoint, or JMT-update bug (including hard-fork-only divergences) would report a clean "matches" result even though the durable state committed to the ledger accumulator differs from the value that would be computed by faithfully replaying the VM. This directly undermines the state-commitment integrity gate: a wrong state root (accumulator/proof-bearing field) is accepted as valid by the very tool meant to detect such divergence, silently masking corruption or fork conditions that operators rely on this tool to surface.

### Likelihood Explanation
The gap is deterministic and always present — it is not conditional on a rare race or attacker input; any invocation of `ensure_match_transaction_info` (in `replay_on_archive`, `aptos-debugger`, or the CLI replay tooling) will unconditionally skip checkpoint-hash comparison. The code itself documents (via the TODO comment) that the gap already exists for the trading-native/hot-state fields, but the omission covers the original `state_checkpoint_hash` field too, which has existed since `TransactionInfoV0`. Any historical or future bug that mismatches the state root while still producing a byte-identical write set — or where write-set materialization races with checkpoint aggregation — will pass this audit undetected.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` between the locally computed checkpoint output and `txn_info`, at least at checkpoint-transaction boundaries (where these fields are `Some`). Since a full per-transaction JMT root recompute may be too costly for arbitrary single transactions, at minimum wire the existing `DoStateCheckpoint`/`get_state_checkpoint_hashes` known-hash validation path into `replay_on_archive`'s block-level replay so the state, hot-state, and position roots are actually asserted against the archived, signature-verified `TransactionInfo` before reporting success.

### Proof of Concept
1. Run `aptos-db-tool replay-on-archive` (or equivalently call `TransactionOutput::ensure_match_transaction_info`) against a backup range where `TransactionInfoV1.state_checkpoint_hash` (or `position_state_checkpoint_hash`) differs from what fresh execution would compute, while keeping the per-transaction `write_set` bytes identical (e.g., simulate a bug where the JMT commit path mis-applies an unrelated pending write, corrupting the root without altering this transaction's own emitted write ops).
2. Observe `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` returns `Ok(None)` for that chunk because `ensure_match_transaction_info` never inspects `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`.
3. The tool reports the archive segment as fully verified even though the committed state root actually diverged from correct VM execution.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-405)
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
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L147-168)
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
```
