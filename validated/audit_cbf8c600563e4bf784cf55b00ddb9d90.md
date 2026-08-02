Confirmed: `state_checkpoint_hash` / `ensure_state_checkpoint_hash` is never referenced anywhere in `storage/db-tool/replay_on_archive.rs` — the state root is never independently verified in that tool's replay path, confirming the gap described by the code's own TODO.

### Title
Replay-verification tooling never checks `state_checkpoint_hash`, allowing a diverged state root to be reported as a successful replay — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` — the sole correctness check used by `db-tool`'s `replay_on_archive` verifier and by the chunk-executor's early replay-verification pass — validates transaction status, gas used, write-set hash, and event-root hash, but explicitly skips comparing the `state_checkpoint_hash` (and `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) carried in the archived `TransactionInfo`. This means a locally re-executed transaction can be reported as matching the archived, ledger-committed `TransactionInfo` even when the resulting authenticated state (Merkle) root diverges from what was actually committed on-chain.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  checks:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set` hash vs `txn_info.state_change_hash()`
- `event_root_hash` (computed from `self.events()`) vs `txn_info.event_root_hash()`

but the function's own comment states it deliberately does **not** check `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

`storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` [2](#0-1)  re-executes archived transactions directly with `AptosVMBlockExecutor::execute_block` and calls only `ensure_match_transaction_info` against the archived `TransactionInfo` — it never independently computes or compares a state-checkpoint/Merkle root. A grep across the file confirms `state_checkpoint_hash` is never referenced there.

The same comparator is also used as the early sanity-check in the chunk-executor's `verify_execution` path [3](#0-2) . In that path, the authoritative check does happen later in `DoStateCheckpoint`'s `get_state_checkpoint_hashes` (which enforces `known[idx] == Some(computed_last_checkpoint_hash)`, see [4](#0-3) ), so the full commit pipeline is protected. However, `db-tool`'s `replay_on_archive` never invokes that downstream checkpoint-hash validation at all — for that tool, the missing check is the *only* check that would ever compare the re-executed state root against the archived one.

### Impact Explanation
`replay_on_archive` exists specifically to audit/verify that historical, authenticated (accumulator-proof-bound) archive data matches independent local re-execution — i.e., a proof-and-storage integrity pivot. Because the comparator silently omits the state-root/checkpoint-hash check, this tool can report "successful replay" for a range of transactions even though the actual state root produced by local execution diverges from the state root that was signed into the ledger's `TransactionInfo`/accumulator. This masks state-integrity divergence (e.g., a state-tree construction bug, non-determinism, or actual on-chain corruption) that the tool is meant to catch, giving false assurance about the correctness of committed/archived ledger state.

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly acknowledged in-code as a known, unresolved TODO, and the only consumer of this comparator for archive verification (`replay_on_archive.rs`) demonstrably never checks state roots elsewhere. Anyone relying on `replay_on_archive` (or the chunk-executor's early-quit verification path) as a state-integrity check today receives an incomplete result whenever a state-root divergence — as opposed to a write-set/event/gas divergence — is the actual issue.

### Recommendation
Extend `ensure_match_transaction_info` (or add a companion check invoked by `replay_on_archive`) to compute the local state-checkpoint hash (and hot-state / position-state checkpoint hashes when applicable) after each re-executed batch and assert equality with `txn_info.state_checkpoint_hash()` / the corresponding fields, before reporting a successful replay.

### Proof of Concept
1. Take an archived backup/segment whose per-transaction `write_set`, `events`, `gas_used`, and `status` are byte-identical to what local re-execution with `AptosVMBlockExecutor` would produce, but for which the state tree actually diverges (e.g., due to a bug that mutates state committed to storage outside the `write_set` path, or a corrupted/incorrectly-restored Jellyfish Merkle Tree).
2. Run `db-tool replay-on-archive` against this data.
3. `execute_and_verify` in [5](#0-4)  calls `ensure_match_transaction_info`, which passes because it only compares status/gas/write-set-hash/event-root-hash.
4. The tool reports zero errors / a successful replay, even though the archived `state_checkpoint_hash` in the corresponding `TransactionInfo` does not match the actual root produced locally — the divergence is never checked or surfaced.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L349-416)
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
}
```

**File:** execution/executor/src/chunk_executor/mod.rs (L648-708)
```rust
    fn verify_execution(
        &self,
        transactions: &[Transaction],
        persisted_aux_info: &[PersistedAuxiliaryInfo],
        transaction_infos: &[TransactionInfo],
        write_sets: &[WriteSet],
        event_vecs: &[Vec<ContractEvent>],
        begin_version: Version,
        end_version: Version,
        verify_execution_mode: &VerifyExecutionMode,
    ) -> Result<Version> {
        // Execute transactions.
        let parent_state = self.commit_queue.lock().latest_state().clone();
        let state_view = self.state_view(parent_state.latest())?;
        let txns = transactions
            .iter()
            .take((end_version - begin_version) as usize)
            .cloned()
            .map(|t| t.into())
            .collect::<Vec<SignatureVerifiedTransaction>>();

        let auxiliary_info = persisted_aux_info
            .iter()
            .take((end_version - begin_version) as usize)
            .map(|persisted_aux_info| AuxiliaryInfo::new(*persisted_aux_info, None))
            .collect::<Vec<_>>();
        let onchain_config = chunk_onchain_config(&state_view)?;
        let execution_output = DoGetExecutionOutput::by_transaction_execution::<V>(
            &V::new(),
            txns.into(),
            auxiliary_info,
            &parent_state,
            state_view,
            onchain_config,
            TransactionSliceMetadata::chunk(begin_version, end_version),
        )?;
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
        Ok(end_version)
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
```
