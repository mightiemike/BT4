Based on my investigation, I found a concrete integrity gap in the replay-verification path that mirrors the external report's core theme: **a status/behavior check is performed while the underlying state-affecting quantities are not actually re-validated before accepting the result as correct.**

### Title
Replay-verify accepts divergent state roots because `ensure_match_transaction_info` never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative per-transaction equivalence check used by the chunk-executor's replay-verify path (`verify_execution`) to confirm that locally re-executed transactions match previously committed, authenticated `TransactionInfo` records. This function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but never compares the state-checkpoint-related hashes carried in `TransactionInfo` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) against the state root actually computed from local execution. As a result, the replay-verify tool can report a clean/successful verification even when the recomputed ledger state root diverges from the one bound into the authenticated `TransactionInfo`/accumulator.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0) . It validates:
- `status()` vs. `txn_info.status()`
- `gas_used()` vs. `txn_info.gas_used()`
- `write_set` hash vs. `txn_info.state_change_hash()`
- event root hash vs. `txn_info.event_root_hash()`

It never touches `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. The function's own trailing comment explicitly documents the gap: [2](#0-1) .

This function is the sole per-transaction correctness check inside `ChunkExecutorInner::verify_execution`, which is invoked whenever `verify_execution_mode.should_verify()` is true during chunk replay: [3](#0-2) . Crucially, `verify_execution` only calls `DoGetExecutionOutput::by_transaction_execution` and then `ensure_match_transaction_info` per transaction — it never invokes `DoStateCheckpoint::run` or `DoLedgerUpdate::run`, which are the only places that actually compute and compare a fresh state-checkpoint root against `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` (as seen in the normal commit path `update_ledger`): [4](#0-3) .

This `verify_execution` mode backs the `replay-verify` tooling paths (`storage/db-tool/src/replay_verify.rs`, `storage/backup/backup-cli/src/coordinators/replay_verify.rs`), which exist specifically to detect state-corruption or non-determinism bugs by replaying historical transactions against backup/archive data and confirming the resulting state matches what was actually committed to the ledger accumulator.

### Impact Explanation
If the VM (or a subsequent code change) produces a state that differs from the one originally committed — while coincidentally preserving the same write-set BCS hash, gas used, status, and event root (e.g., a bug purely in state-checkpoint/hot-state/position-tree root computation, or an execution divergence that doesn't change the raw write set but changes how it's merklized) — `verify_execution` will still report success. This directly undermines the state-integrity guarantee the replay-verify tool is meant to provide: "authenticated API or state-view output bound to the wrong version/root" is exactly the class this check is supposed to catch and doesn't. It's a detection-of-corruption gap rather than a live path that itself corrupts committed mainnet state (the actual commit path does perform full checkpoint-hash validation via `DoStateCheckpoint`), so severity is bounded to failure of an integrity backstop rather than direct mainnet state corruption.

### Likelihood Explanation
Medium. The gap is triggered any time `verify_execution_mode.should_verify()` runs (routine for `db-tool replay-verify` and backup coordinator flows), and it is unconditionally present for every verified transaction rather than depending on rare edge cases. The condition needed to actually exploit the gap (write-set/event/gas/status matching while checkpoint hash diverges) is narrower, but is exactly the scenario new trading-native/hot-state root features (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) introduce risk for, since those roots are computed from write-set contents rather than being part of the hashed write set itself.

### Recommendation
Extend `ensure_match_transaction_info` (or add a parallel check invoked from `verify_execution`) to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields in the provided `TransactionInfo`, mirroring the checks already performed by `DoStateCheckpoint`/`DoLedgerUpdate` in the live commit path. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet, as the code comment itself anticipates.

### Proof of Concept
Conceptual PoC (requires a Devin session with repo access to construct/run):
1. Build a chunk of historical transactions plus corresponding `TransactionInfo`s where the write set, events, gas, and status are identical to what's committed, but craft an execution environment where the resulting `state_checkpoint_hash` legitimately differs (e.g., feed a different `persisted_state_summary` base, or a build with a state-checkpoint hashing bug).
2. Run `ChunkExecutorInner::verify_execution` (or the `db-tool replay-verify` CLI) against this chunk.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` for every transaction and `verify_execution` reports success (`Ok(end_version)`), despite the state root diverging from the authenticated `TransactionInfo`. [1](#0-0) [3](#0-2) [4](#0-3)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L363-419)
```rust
    pub fn update_ledger(&self) -> Result<()> {
        let _timer = CHUNK_OTHER_TIMERS.timer_with(&["chunk_update_ledger_total"]);

        let (parent_state_summary, parent_position_state_summary, parent_accumulator, chunk) =
            self.commit_queue.lock().next_chunk_to_update_ledger()?;
        let ChunkToUpdateLedger {
            output,
            chunk_verifier,
        } = chunk;

        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;

        let ledger_update_output = DoLedgerUpdate::run(
            &output.execution_output,
            &state_checkpoint_output,
            parent_accumulator.clone(),
        )?;
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
