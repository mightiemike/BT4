### Title
`TransactionOutput::ensure_match_transaction_info` skips state/hot-state/position checkpoint hash validation, letting chunk-replay and backup-restore verification accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
The self-documented TODO in `types/src/transaction/mod.rs` at the `ensure_match_transaction_info` function states that the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the function used to validate a freshly re-executed `TransactionOutput` against a previously-persisted/authenticated `TransactionInfo` during replay: [2](#0-1) 

It checks `status`, `gas_used`, `write_set_hash` (`state_change_hash`), and `event_root_hash` — but never compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against a hash computed from the locally rebuilt state tree(s).

This function is called from `ChunkExecutor::verify_execution` in `execution/executor/src/chunk_executor/mod.rs`, which is exercised by:
- `TransactionReplayer::remove_and_replay_epoch` / `verify_execution` (used by `db-tool replay-verify` / `replay_on_archive` and `aptos-debugger`) [3](#0-2) 
- The backup-restore transaction replay path in `storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, which drives a `ChunkExecutor` over a restored ledger and enqueues chunks for `verify_execution_mode` [4](#0-3) 

Because the state-tree root fields carried in `TransactionInfo` (state checkpoint hash, hot-state checkpoint hash, and the newly repurposed `position_state_checkpoint_hash` used for the "trading-native" position-state SMT, per `do_state_checkpoint.rs`) are excluded from this comparison, a divergence between the locally-rebuilt Jellyfish/position-state Merkle tree and the historically-recorded root will not be caught by `verify_execution`/replay-verify. The accumulator itself is still rebuilt from `TransactionInfo::hash()` (which does include these fields), but the *actual rebuilt state tree contents* on the local, restoring, or replay-verifying node are never independently confronted against those hash fields at the point they are recomputed — only implicitly, if a later full accumulator hash check is performed against a signed `LedgerInfo`. [5](#0-4) 

### Impact Explanation
The affected paths (`db-tool replay-verify`/`replay_on_archive`, `aptos-debugger`, and backup restore's chunk-replay) are exactly the tools operators and auditors rely on to assert that a restored or replayed ledger's state matches the historically committed one bit-for-bit, including the state/hot-state/position-state Merkle roots. Because these tools' pass/fail signal is derived from `ensure_match_transaction_info`, a bug in state-tree reconstruction (e.g., in the hot-state or position-state SMT logic gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) can silently produce a corrupted, wrong state root in the restored/replayed database while `replay-verify` reports success. This directly matches the required "Restore paths must not reinterpret committed data into a different ledger state" and "Wrong ... state proof accepted as valid" impact classes, since a wrong state-commitment root can be accepted as verified. It is a durable-state integrity gap in offline verification tooling rather than in the online, validator-signed consensus/state-sync path (which still binds these hashes into the transaction-accumulator hash checked against a signed `LedgerInfo`).

### Likelihood Explanation
The condition requires a latent bug (present or future) in state/hot-state/position-state tree reconstruction that changes the resulting root without changing the write set, events, or gas used — plausible given this is actively evolving code (the position-state checkpoint hash is a "repurposed reserved field" backing a newly enabled trading-native root feature). Given the code comment is an explicit, self-acknowledged gap left unresolved before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, the likelihood of this gap materializing into an undetected replay/restore divergence increases as that feature is rolled out.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when available) against locally recomputed values before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, and fail replay/restore verification on divergence, matching the enforcement already implicit in the accumulator/LedgerInfo path.

### Proof of Concept
Not applicable as a runnable exploit — the finding is a code-level gap: `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2139-2204` never reads `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, and both `execution/executor/src/chunk_executor/mod.rs:692-697` (`verify_execution`) and the backup-restore replay pipeline in `storage/backup/backup-cli/src/backup_types/transaction/restore.rs:686-694` rely solely on this function to validate replayed output, confirmed via direct reading of the function bodies and call sites cited above.

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

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L663-699)
```rust
    ) -> Result<()> {
        let (first_version, _) = self.replay_from_version.unwrap();
        restore_handler.reset_state_store();
        let replay_start = Instant::now();
        let db = DbReaderWriter::from_arc(Arc::clone(&restore_handler.aptosdb));
        let chunk_replayer = Arc::new(ChunkExecutor::<AptosVMBlockExecutor>::new(db));
        let ledger_update_stream = txns_to_execute_stream
            .try_chunks(BATCH_SIZE)
            .err_into::<anyhow::Error>()
            .map_ok(|chunk| {
                let (txns, persisted_aux_info, txn_infos, write_sets, events): (
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                    Vec<_>,
                ) = chunk.into_iter().multiunzip();
                let chunk_replayer = chunk_replayer.clone();
                let verify_execution_mode = self.verify_execution_mode.clone();

                async move {
                    let _timer = OTHER_TIMERS_SECONDS.timer_with(&["enqueue_chunks"]);

                    tokio::task::spawn_blocking(move || {
                        chunk_replayer.enqueue_chunks(
                            txns,
                            persisted_aux_info,
                            txn_infos,
                            write_sets,
                            events,
                            &verify_execution_mode,
                        )
                    })
                    .await
                    .expect("spawn_blocking failed")
                }
            })
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
