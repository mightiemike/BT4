## Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify tooling accept a divergent state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the routine used by the chunk-executor's replay-verification path and by CLI/db-tool replay debuggers to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` stored on-chain. As explicitly acknowledged in the code's own TODO, this function checks status, gas used, write-set hash, and event root hash, but **does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`** — the fields that bind the JMT/state-tree roots into the authenticated, accumulator-committed `TransactionInfo`.

### Finding Description
`TransactionInfo::V1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` [1](#0-0) , and these hashes are derived from `do_state_checkpoint.rs`'s `LedgerStateSummary`/position-summary root-hash computation each time a checkpoint boundary is reached [2](#0-1) . These per-checkpoint roots are exactly the values that are supposed to prove state-commitment correctness independent of the write-set hash (which only proves the *ops emitted*, not the resulting tree state).

However, `ensure_match_transaction_info` — the single comparator used to validate a re-executed `TransactionOutput` against a previously-committed `TransactionInfo` — only asserts equality of `status`, `gas_used`, `write_set_hash` (vs `state_change_hash`), and `event_root_hash`, and explicitly documents (via its own TODO) that it "ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [3](#0-2) .

This comparator is exercised on the state-integrity-critical replay path in the chunk executor: `TransactionReplayer::verify_execution` re-executes a batch of transactions and calls `ensure_match_transaction_info` against the previously persisted `transaction_infos` to decide whether replay succeeded [4](#0-3) , and the same function is invoked by the CLI transaction debugger/replay tooling [5](#0-4)  and by `storage/db-tool/src/replay_on_archive.rs`'s verification loop [6](#0-5) .

### Impact Explanation
Because the write-set hash only commits to the *emitted write operations*, not to the resulting state-tree root, a bug anywhere in state-tree construction, JMT node placement, hot-state summary update, or (once enabled) the native "position" state tree used for `COMPUTE_TRADING_NATIVE_STATE_ROOTS` could silently produce a different `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` at a checkpoint boundary than what is actually anchored in the ledger's authenticated `TransactionInfo`, while `ensure_match_transaction_info` still reports success. This means:
- Chunk-executor replay verification (`verify_execution_mode.should_verify()`) can mask a real state-commitment divergence introduced by executor bugs, effectively defeating one of its purposes — detecting hard-fork-only divergence during replay.
- Operational/db-tool replay-verify workflows used to audit historical state roots against the authenticated chain can report false positives, masking a corrupted or incorrectly reconstructed ledger state.

This falls squarely in scope as a "hard-fork-only divergence during commit, replay, restore, or proof verification" and "authenticated API or state-view output bound to the wrong version, object, or proof context" per the stated gate, because the comparator is meant to be the authenticated cross-check between locally computed state and the on-chain-committed state root.

### Likelihood Explanation
The gap is unconditional in current code — it's not gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` alone; `state_checkpoint_hash` and `hot_state_checkpoint_hash` (which are always present in `TransactionInfo::V1` for ordinary checkpoints) are also never checked. The bug requires a *separate*, pre-existing state-computation divergence to actually manifest an undetected on-chain state-root difference; by itself, `ensure_match_transaction_info` does not corrupt data, it only fails to detect corruption that already occurred elsewhere. Likelihood of triggering visible harm therefore depends on a second root-cause bug in state-tree computation, but the detection gap itself is a concrete, self-acknowledged, always-present weakness in an integrity-verification code path that is unprivileged (any node/tool running replay-verify is affected).

### Recommendation
In `ensure_match_transaction_info`, add checks comparing the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when the executed output actually produces a checkpoint) against the corresponding fields on the supplied `txn_info`, consistent with the pattern already used for `state_change_hash`/`event_root_hash`. This should be done before, not after, enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the code comment itself recommends.

### Proof of Concept
Not independently reproducible without a companion state-tree computation bug (the code's own comment already documents the exact scenario): run `db-tool replay-on-archive` (or the chunk executor's `verify_execution`) against a range of transactions where, due to a hypothetical divergence in state-checkpoint construction, the locally recomputed state/hot-state/position root differs from the one embedded in the archived `TransactionInfo`. Because `ensure_match_transaction_info` never compares these hash fields [7](#0-6) , the verification call reports success despite the state-root mismatch.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-60)
```rust
        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;
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

**File:** aptos-move/cli/src/commands.rs (L2797-2813)
```rust
        // Materialize into transaction output and check if the outputs match.
        let txn_output = vm_output.into_transaction_output().map_err(|err| {
            CliError::UnexpectedError(format!(
                "Failed to materialize into transaction output: {}",
                err
            ))
        })?;

        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L242-314)
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
