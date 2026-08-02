This confirms the finding: `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` uses `TransactionOutput::execute_block` to re-execute a chunk from an untrusted backup archive, and validates the recomputed output against the archive-supplied `expected_txn_infos[idx]` solely via `ensure_match_transaction_info` [1](#0-0) . That comparator checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly and intentionally skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — all of which are fields of the same `TransactionInfo` object being "verified" [2](#0-1) .

### Title
Replay-verify tooling accepts archives with a forged/incorrect state-checkpoint (Merkle root) `TransactionInfo` field - (`File: types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the sole correctness check used by `db-tool replay-on-archive` and referenced by `backup-cli`'s replay-verify flow, never compares the re-executed `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` against the archive-provided `TransactionInfo`. An archive whose transaction bodies, events, and write-sets are legitimate but whose checkpoint-hash fields have been corrupted or forged will pass "replay verification" successfully.

### Finding Description
`ensure_match_transaction_info` validates 4 fields: `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash` [3](#0-2) . It never fetches or compares the locally-computed state Merkle root (produced separately by `DoStateCheckpoint`/`assemble_transaction_infos` during normal block execution) against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. The code contains an explicit acknowledgment of this gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [4](#0-3) 

Crucially, `replay_on_archive.rs::execute_and_verify` calls `execute_block` (which produces plain `TransactionOutput`s, not `TransactionInfo`s carrying a checkpoint root) and then calls `ensure_match_transaction_info` as the *only* pass/fail gate before marking the chunk as verified [5](#0-4) . Because the state/hot-state checkpoint root is never independently recomputed and compared in this tool's path, a divergence in the Sparse-Merkle-Tree state root between the archive's claimed `TransactionInfo` and the actual post-execution ledger state is invisible to this verification pass.

### Impact Explanation
Replay-verify (`replay-on-archive` / backup-cli's `replay_verify`) is the tool operators and auditors rely on to authenticate that a backup archive's committed ledger state matches independent re-execution — i.e., the state-commitment/proof-integrity invariant this task asks about. If the state checkpoint hash (the SMT root binding all account/resource values at a checkpoint) is not checked, a corrupted or tampered archive/backup can be certified as "verified" even though its authenticated state root does not match what real execution produces. This falls squarely in the "wrong accumulator root ... accepted as valid" and "replay/restore ... proof-integrity" impact bucket in the gate, because downstream consumers (auditors, restore pipelines, or nodes bootstrapping from this archive) would trust a root that doesn't reflect actual VM output.

### Likelihood Explanation
This requires an archive with an already-forged/corrupted `TransactionInfo` checkpoint-hash field (e.g., a compromised or buggy backup source), which limits it to backup/restore/replay-verify workflows rather than live consensus commit. It's also explicitly flagged by the authors as tied to `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (trading-native/position-state feature), which I could not confirm is enabled by default on mainnet — I was unable to fully trace the feature-flag default state or find another codepath that re-validates `state_checkpoint_hash`/`hot_state_checkpoint_hash` independently for this specific tool before reaching the final tool-call limit. Given the explicit TODO acknowledging the exact gap, this is a genuine, currently-existing verification gap in the replay-verify tool, but its severity depends on how much operators rely on this specific tool (rather than live-node consensus, which authenticates checkpoints via signed `LedgerInfo` over the full accumulator, not via this comparator) — this significantly limits it to auditing/tooling risk rather than a live consensus-halting or fund-loss primitive.

### Recommendation
Extend `ensure_match_transaction_info` (or `execute_and_verify` in `replay_on_archive.rs`) to independently recompute the state/hot-state/position-state checkpoint roots for the re-executed chunk (via the same `DoStateCheckpoint` logic used in the executor) and compare them against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` before declaring a chunk verified.

### Proof of Concept
Not independently reproducible from static analysis alone within the given tool budget — the exact conditions under which `replay_on_archive`/backup-cli's replay-verify is used against untrusted/attacker-influenced archives, and whether `hot_state_checkpoint_hash`/`state_checkpoint_hash` are otherwise cross-checked via a different mechanism (e.g., signed `LedgerInfo` verification prior to invoking this tool), were not fully confirmed before the tool-call limit was reached. The static code evidence (the explicit TODO plus the field-by-field comparator) is the basis for this finding; a background engineering session with test-execution capability would be needed to construct a concrete forged-archive PoC and confirm whether any other layer catches the mismatch.

### Citations

**File:** storage/db-tool/src/replay_on_archive.rs (L349-415)
```rust
    fn execute_and_verify(
        &self,
        executor: &AptosVMBlockExecutor,
        current_version: &mut Version,
        cur_txns: &mut Vec<Transaction>,
        cur_persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        expected_txn_infos: &mut Vec<TransactionInfo>,
        expected_events: &mut Vec<Vec<ContractEvent>>,
        expected_writesets: &mut Vec<WriteSet>,
    ) -> Result<Option<Error>> {
        if cur_txns.is_empty() {
            return Ok(None);
        }
        let txns = cur_txns
            .iter()
            .map(|txn| SignatureVerifiedTransaction::from(txn.clone()))
            .collect::<Vec<_>>();
        let txns_provider = DefaultTxnProvider::new(
            txns,
            cur_persisted_aux_info
                .iter()
                .map(|info| AuxiliaryInfo::new(*info, None))
                .collect(),
        );
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

        cur_txns.clear();
        cur_persisted_aux_info.clear();
        expected_txn_infos.clear();
        expected_events.clear();
        expected_writesets.clear();

        Ok(None)
    }
```

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
