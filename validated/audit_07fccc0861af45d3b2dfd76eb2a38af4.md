### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint hash verification, letting corrupted state roots pass execution-verification and replay-verify checks - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-comparison routine used by the chunk executor's execution-verification path and by the `replay_on_archive`/`replay_verify` DB tooling to confirm that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` recorded in the ledger/backup. The function checks status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`). This is the exact same bug class as the reported H-7 issue: an integrity-critical running/derived value (`totalCdsDepositedAmount` there, the state-checkpoint/state-root hash here) is silently excluded from the deduction/verification logic that is supposed to keep it consistent, so a divergence goes undetected downstream.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` (lines 2139–2204) is documented by its own code comment as incomplete: [1](#0-0) 

It verifies:
1. `status()` vs. `txn_info.status()`
2. `gas_used()` vs. `txn_info.gas_used()`
3. `CryptoHash::hash(write_set)` vs. `txn_info.state_change_hash()`
4. Event root hash vs. `txn_info.event_root_hash()`

It never compares the state checkpoint hash (`txn_info.state_checkpoint_hash()`), the hot-state checkpoint hash, or the position-state checkpoint hash against anything computed from re-execution. The comment explicitly states this is a known gap: "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is called from two integrity-critical, unprivileged-facing paths that any operator/verifier relies on to detect ledger corruption or non-determinism:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, which drives `VerifyExecutionMode::Verify` used during state-sync chunk application and backup replay verification. [2](#0-1) 
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, which is the tool operators use to independently re-execute historical transactions from an archive and confirm the resulting output matches what is stored, i.e., a proof-integrity self-check over the authenticated `TransactionInfo`. [3](#0-2) 

Because the state-checkpoint hash is exactly the field that binds a `TransactionInfo` (and thus the transaction accumulator/ledger info it's proven against) to the actual post-execution state root (Jellyfish Merkle root), omitting it from this comparison means a re-executed state root that differs from the one recorded on-chain will not be flagged as a mismatch by either `verify_execution` or `replay_on_archive`. Both `Verify { .. }` chunk-execution verification and the replay-verify tool exist specifically to catch determinism bugs, storage corruption, or malicious archive data — and this exact class of divergence is silently accepted.

### Impact Explanation
This breaks the "Authenticated API or state-view output bound to the wrong version, object, or proof context" and "committed state that differs from the correct VM result" invariants called out in the state-integrity gate. A node operator running `replay_verify`/`replay_on_archive` against an archive, or a chunk executor validating chunks under `VerifyExecutionMode::Verify`, can be told "replay succeeded" even though the resulting state root (state checkpoint hash) does not match the state root committed in the `TransactionInfo`/ledger info. This masks:
- Non-determinism or bugs in the VM/state-checkpoint computation that produce a different final state than what was originally committed on mainnet.
- Corrupted or tampered write-set/state data in a backup/archive that still happens to produce the same write-set hash coincidentally at the per-txn granularity but a divergent aggregate state tree.
- Divergences in the new "trading-native" state-root paths (hot-state / position-state checkpoint hashes) introduced alongside `TRANSACTION_INFO_V1`, exactly as the TODO warns before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

Because this is a verification/proof-integrity gap rather than a live consensus-path check, its severity is high (undermining a core detection mechanism for ledger-state divergence and forks during replay/verify/restore) though it does not by itself corrupt consensus-committed state.

### Likelihood Explanation
This code path executes on every chunk-execution verification and every `replay_on_archive`/`replay_verify` invocation — these are standard operator tools for backup validation and disaster recovery, not privileged or exotic flows. The gap is unconditionally present (not merely as a fallback); any bug or discrepancy that affects only the state-checkpoint hash (and not the write-set hash itself, e.g. issues in `DoStateCheckpoint`, JMT construction, or hot/position-state root computation) would go completely undetected by this verification, defeating its purpose.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash` (and, once trading-native state roots are enabled, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the value produced from re-execution, mirroring the checks already done for `state_change_hash` and `event_root_hash`. Since `TransactionOutput` alone does not carry the state-checkpoint hash, the caller (`chunk_executor::verify_execution` / `replay_on_archive::execute_and_verify`) needs to be updated to pass through the state-checkpoint hash computed by `DoStateCheckpoint` for the corresponding transaction so it can be compared.

### Proof of Concept
No PoC executable artifact was produced; the finding is established via static code inspection:
1. `ensure_match_transaction_info` implementation showing only 4 checks and the explicit TODO acknowledging the gap: [4](#0-3) 
2. Its use in `chunk_executor::verify_execution`, the sole consistency gate for `VerifyExecutionMode::Verify`: [5](#0-4) 
3. Its use in `replay_on_archive::execute_and_verify`, the operator-facing archive replay verification tool: [6](#0-5) 

Note: I was unable to fully explore `chunk_result_verifier.rs` (which handles a related but distinct verification path for state-sync chunk commits) before the tool budget ran out, so it is possible — though not confirmed — that a separate accumulator/root-hash check there partially mitigates impact in that specific flow. The `replay_on_archive.rs` and `chunk_executor::verify_execution` paths, however, are confirmed to rely solely on `ensure_match_transaction_info` with no supplementary state-root check visible in the surrounding code.

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
