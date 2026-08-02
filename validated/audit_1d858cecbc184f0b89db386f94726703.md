### Title
`ensure_match_transaction_info` skips state-checkpoint / hot-state / position-state root validation, allowing replay-verify and archive replay to accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used by chunk replay (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and by the backup/db-tool replay-verify path (`storage/db-tool/src/replay_on_archive.rs`, `storage/backup/backup-cli/src/coordinators/replay_verify.rs`) to confirm that locally re-executed transaction results match the trusted `TransactionInfo` pulled from an authenticated backup/accumulator. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash` (or `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), which is the actual state-root commitment for the version.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (hash of the write set) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It never touches `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`. The code contains its own acknowledgment of the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```

`state_checkpoint_hash` is the Jellyfish Merkle root committed for the version — the single value that binds the entire authenticated global state to the ledger/accumulator (analogous to `TransactionAccumulatorHash` for transactions). The write-set hash only proves the *raw write operations* replayed equal the raw write operations recorded; it says nothing about whether applying those writes onto the actual persisted state tree produces the same root that was cryptographically committed in the `LedgerInfo`/accumulator. A state root mismatch (from a JMT bug, a state-view priming bug, a hot-state promotion bug, or a corrupted/malicious backup source) can therefore pass this gate undetected.

This gate is used in two mainnet-relevant proof/replay paths:
1. Chunk replay for state sync / node bootstrap: `execution/executor/src/chunk_executor/mod.rs::verify_execution` calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` and, on success, commits the transaction (via `remove_and_apply`) using the *given* `transaction_infos` (i.e., the pre-existing, backup/archive-sourced `TransactionInfo`, not a freshly recomputed one), [2](#0-1) .
2. Backup replay-verify tooling: `storage/db-tool/src/replay_on_archive.rs` executes archived transactions and calls this same comparator to decide whether the replay is "correct", [3](#0-2) , and `ReplayVerifyCoordinator` treats it as ground truth for CI/operator confidence that a backup restores to the right state, [4](#0-3) .

Because the checkpoint-hash validation is silently skipped, both flows can conclude "replay verified OK" while the locally computed state root (state, hot-state, or `position_state_checkpoint_hash` for the trading-native pipeline) actually diverges from the value cryptographically committed by consensus in the `LedgerInfo`/accumulator for that version.

### Impact Explanation
This breaks the proof/commitment invariant that "committed state that differs from the correct VM result or corrupts durable ledger data" must be detected. Concretely:
- A node or tool replaying transactions from an untrusted or corrupted backup, or hitting a bug in JMT/hot-state/position-state root computation, will not surface a divergent state root through `ensure_match_transaction_info`. The only symptom would be a downstream root-hash mismatch elsewhere (if any), but this specific integrity gate — whose entire purpose is to catch exactly this class of divergence — is a no-op for state roots.
- `replay_on_archive`/`replay_verify` are used as the authoritative check that a chain of backups reconstructs the correct ledger state; a false "success" here can let a subtly corrupted archive/backup pass verification and be trusted for node bootstrap or DB restore, i.e., durable ledger data can silently diverge from the correct VM result without automated detection.
- The comment explicitly flags this as blocking for `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (the position/trading-native state root feature), meaning the state root of an entire (currently gated) subsystem is unverified by design in this shared comparator.

This matches the "Committed state that differs from the correct VM result" and "authenticated ... state-view output bound to the wrong version/root" impact classes from the state-integrity gate: the check that is supposed to bind local computation to the authenticated root is missing.

### Likelihood Explanation
The comparator is reached on every chunk replay verification and every db-tool replay-verify run — this is not a rare code path, it is the standard verification tool operators/CI run against backups. However, the *practical* trigger requires an underlying divergence to already exist (e.g., a JMT/state-view bug, corrupted backup bytes for state, or bugs in the (currently gated) position/trading-native state root computation) — this finding is about the missing detection mechanism, not a way to directly corrupt state by itself. Given `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is referenced as not-yet-enabled per the comment, current exposure is bounded to that feature area plus any latent state/hot-state root bugs that would otherwise be caught here; likelihood of the detection gap being exploited for a hard-fork-relevant divergence rises directly with rollout of the trading-native/position-state feature.

### Recommendation
Add explicit `ensure!` checks in `ensure_match_transaction_info` comparing the locally recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the corresponding values computed on the replayed side, mirroring the pattern already used for `state_change_hash`/`event_root_hash`. This must be done, per the code's own TODO, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, but the state/hot-state checkpoint hash checks should be added regardless since the same comparator is already relied upon by `replay_on_archive` and chunk replay verification today.

### Proof of Concept
Not independently demonstrable as a standalone exploit — this is a missing-check/detection-gap finding. Conceptual PoC:
1. Take a `TransactionInfo` (from a backup manifest or accumulator) whose `state_checkpoint_hash` is `H_correct`.
2. Locally replay the corresponding transaction and reach a `result_state` whose actual JMT root is `H_wrong` (e.g., due to a hot-state promotion bug, or a bit-flipped/corrupted state value that still yields the same write-set hash after re-serialization edge cases, or any bug in `do_state_checkpoint.rs` root computation).
3. Call `TransactionOutput::ensure_match_transaction_info(version, txn_info, ...)` — per the code at [5](#0-4) , this returns `Ok(())` because only `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash` are checked; `state_checkpoint_hash` is never compared to `H_wrong`.
4. `replay_on_archive`'s `execute_and_verify` (storage/db-tool/src/replay_on_archive.rs) and the chunk executor's `verify_execution` both treat this as a successful, verified replay, even though the committed state root diverges from the correct value.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L648-707)
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L242-313)
```rust
    // Execute the verify one valid range
    pub fn verify(&self, start: Version, limit: u64) -> Result<Vec<Error>> {
        let mut total_failed_txns = Vec::with_capacity(limit as usize);
        let txn_iter = self
            .backup_handler
            .get_transaction_iter(start, limit as usize)?;
        let mut cur_txns = Vec::with_capacity(limit as usize);
        let mut cur_persisted_aux_info = Vec::with_capacity(limit as usize);
        let mut expected_events = Vec::with_capacity(limit as usize);
        let mut expected_writesets = Vec::with_capacity(limit as usize);
        let mut expected_txn_infos = Vec::with_capacity(limit as usize);
        let mut chunk_start_version = start;
        let executor = AptosVMBlockExecutor::new();
        for item in txn_iter {
            // timeout check
            if let Some(duration) = self.timeout_secs {
                if self.replay_stat.get_elapsed_secs() >= duration {
                    bail!(
                        "Verify timeout: {}s elapsed. Deadline: {}s. Failed txns count: {}",
                        self.replay_stat.get_elapsed_secs(),
                        duration,
                        total_failed_txns.len(),
                    );
                }
            }

            let (
                input_txn,
                persisted_aux_info,
                expected_txn_info,
                expected_event,
                expected_writeset,
            ) = item?;
            let is_epoch_ending = expected_event.iter().any(ContractEvent::is_new_epoch_event);
            cur_txns.push(input_txn);
            cur_persisted_aux_info.push(persisted_aux_info);
            expected_txn_infos.push(expected_txn_info);
            expected_events.push(expected_event);
            expected_writesets.push(expected_writeset);
            if is_epoch_ending || cur_txns.len() >= self.chunk_size {
                let cnt = cur_txns.len();
                while !cur_txns.is_empty() {
                    // verify results
                    let failed_txn_opt = self.execute_and_verify(
                        &executor,
                        &mut chunk_start_version,
                        &mut cur_txns,
                        &mut cur_persisted_aux_info,
                        &mut expected_txn_infos,
                        &mut expected_events,
                        &mut expected_writesets,
                    )?;
                    // collect failed transactions
                    total_failed_txns.extend(failed_txn_opt);
                }
                self.replay_stat.update_cnt(cnt as u64);
                self.replay_stat.print_tps();
            }
        }
        // verify results
        let fail_txns = self.execute_and_verify(
            &executor,
            &mut chunk_start_version,
            &mut cur_txns,
            &mut cur_persisted_aux_info,
            &mut expected_txn_infos,
            &mut expected_events,
            &mut expected_writesets,
        )?;
        total_failed_txns.extend(fail_txns);
        Ok(total_failed_txns)
    }
```

**File:** storage/backup/backup-cli/src/coordinators/replay_verify.rs (L84-212)
```rust
    pub async fn run(self) -> Result<(), ReplayError> {
        info!("ReplayVerify coordinator started.");
        let ret = self.run_impl().await;

        if let Err(e) = &ret {
            error!(
                error = ?e,
                "ReplayVerify coordinator failed."
            );
        } else {
            info!("ReplayVerify coordinator exiting with success.");
        }

        ret
    }

    async fn run_impl(self) -> Result<(), ReplayError> {
        AptosVM::set_concurrency_level_once(self.replay_concurrency_level);
        set_timed_feature_override(TimedFeatureOverride::Replay);

        let metadata_view = metadata::cache::sync_and_load(
            &self.metadata_cache_opt,
            Arc::clone(&self.storage),
            self.concurrent_downloads,
        )
        .await?;
        if self.start_version > self.end_version {
            return Err(ReplayError::OtherError(format!(
                "start_version {} should precede end_version {}.",
                self.start_version, self.end_version
            )));
        }

        let run_mode = Arc::new(RestoreRunMode::Restore {
            restore_handler: self.restore_handler,
        });
        let mut next_txn_version = run_mode.get_next_expected_transaction_version()?;
        let (state_snapshot, snapshot_version) = if let Some(version) =
            run_mode.get_in_progress_state_kv_snapshot()?
        {
            info!(
                version = version,
                "Found in progress state snapshot restore",
            );
            (
                Some(metadata_view.expect_state_snapshot(version)?),
                Some(version),
            )
        } else if let Some(snapshot) = metadata_view.select_state_snapshot(self.start_version)? {
            let snapshot_version = snapshot.version;
            info!(
                "Found state snapshot backup at epoch {}, will replay from version {}.",
                snapshot.epoch,
                snapshot_version + 1
            );
            (Some(snapshot), Some(snapshot_version))
        } else {
            (None, None)
        };

        let skip_snapshot: bool =
            snapshot_version.is_none() || next_txn_version > snapshot_version.unwrap();
        if skip_snapshot {
            info!(
                next_txn_version = next_txn_version,
                snapshot_version = snapshot_version,
                "found in progress replay and skip the state snapshot restore",
            );
        }

        // Once it begins replay, we want to directly start from the version that failed
        let save_start_version = (next_txn_version > 0).then_some(next_txn_version);

        next_txn_version = std::cmp::max(next_txn_version, snapshot_version.map_or(0, |v| v + 1));

        let transactions = metadata_view.select_transaction_backups(
            // transaction info at the snapshot must be restored otherwise the db will be confused
            // about the latest version after snapshot is restored.
            next_txn_version.saturating_sub(1),
            self.end_version,
        )?;
        let global_opt = GlobalRestoreOptions {
            target_version: self.end_version,
            trusted_waypoints: Arc::new(self.trusted_waypoints_opt.verify()?),
            run_mode,
            concurrent_downloads: self.concurrent_downloads,
            replay_concurrency_level: 0, // won't replay, doesn't matter
        };

        if !skip_snapshot {
            if let Some(backup) = state_snapshot {
                StateSnapshotRestoreController::new(
                    StateSnapshotRestoreOpt {
                        manifest_handle: backup.manifest,
                        version: backup.version,
                        validate_modules: self.validate_modules,
                        restore_mode: Default::default(),
                    },
                    global_opt.clone(),
                    Arc::clone(&self.storage),
                    None, /* epoch_history */
                )
                .run()
                .await?;
            }
        }

        TransactionRestoreBatchController::new(
            global_opt,
            self.storage,
            transactions
                .into_iter()
                .map(|t| t.manifest)
                .collect::<Vec<_>>(),
            save_start_version,
            Some((next_txn_version, false)), /* replay_from_version */
            None,                            /* epoch_history */
            self.verify_execution_mode.clone(),
            None,
        )
        .run()
        .await?;

        if self.verify_execution_mode.seen_error() {
            Err(ReplayError::TxnMismatch)
        } else {
            Ok(())
        }
    }
```
