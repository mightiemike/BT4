This confirms the actual state-commitment path (`LedgerUpdateOutput::ensure_transaction_infos_match` at [1](#0-0)  and `DoStateCheckpoint`'s known-hash comparison against `computed_last_checkpoint_hash` at [2](#0-1) ) does compare full `TransactionInfo`, including `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. So the primary consensus/state-sync commit path is intact.

The gap is isolated to the separate transaction-*replay-verification* path, which reuses `TransactionOutput::ensure_match_transaction_info` — and its own inline `TODO` comment documents that it deliberately skips checkpoint-hash comparison.

### Title
Replay-verification comparator omits checkpoint-hash checks, allowing divergent position/state roots to be accepted as a "successful" verified replay - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by transaction-replay verification tooling (`ChunkExecutor::verify_execution` in the backup restore/replayer path, and `db-tool`'s `replay_on_archive`). It checks transaction status, gas, write-set hash, and event-root hash against a `TransactionInfo`, but does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code's own comment acknowledges.

### Finding Description
`ensure_match_transaction_info` at [3](#0-2)  validates only `status`, `gas_used`, write-set hash (`state_change_hash`), and `event_root_hash`. The trailing comment explicitly states: [4](#0-3) 

This function is invoked as the pass/fail criterion in `ChunkExecutorInner::verify_execution`, used during backup-restore replay (`storage/backup/backup-cli/.../transaction/restore.rs`), and it is the verification method backing `db-tool`'s `replay_on_archive::verify` at [5](#0-4)  (used via `execute_and_verify`). It is invoked at [6](#0-5) .

By contrast, the actual commit path is safe: `DoStateCheckpoint::get_state_checkpoint_hashes` compares `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` (each sourced from the archived `TransactionInfo`) against the freshly recomputed root at [7](#0-6) , and `LedgerUpdateOutput::ensure_transaction_infos_match` performs a full `TransactionInfo` equality check [1](#0-0) . So a *live* node applying transactions from consensus or from a state-sync chunk cannot silently accept a wrong state root — that invariant is preserved.

The break is specifically in the **replay-verify (audit) tooling**: an operator running `db-tool replay-on-archive verify` or the backup restore-with-verification flow to confirm that an archived transaction history reproduces the correct ledger state can get a "success" result even when the locally recomputed state/hot-state/position-state roots (bound to `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state features) diverge from what is recorded in the archived `TransactionInfo`. This can hide state divergence from execution changes, feature-flag rollout bugs, or storage corruption in exactly the checkpoint fields that matter most.

### Impact Explanation
This falls under "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "authenticated ... proof-bearing responses must stay bound to the right ledger version, root, and object." Replay verification is one of the load-bearing tools operators and auditors use to confirm that a restored/archived chain segment reproduces the canonical ledger state (state root, hot-state root, position-state root) before trusting or serving it. Because `ensure_match_transaction_info` never compares these checkpoint hashes, a divergence in state-commitment (e.g., a bug in the trading-native/position-state feature, or corrupted archived data) is not detected by the tool whose entire purpose is to detect exactly that. This is a proof/state-integrity gap in an authenticated verification pathway, not merely an aesthetic gap, since it can mask genuinely differing committed state as "verified correct."

### Likelihood Explanation
The comment marks this as a known, intentional gap tied to the not-yet-fully-rolled-out `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature — it is not a live mainnet exploit today because that feature flag path is what the comment says needs validation "before enabling." However, since the code is present and reachable by both `db-tool replay_on_archive` and backup-restore verification (both used against real, potentially mainnet, archived data), the likelihood of this masking a real state divergence increases proportionally as those checkpoint types (hot state, position/trading-native state) are activated on mainnet. It is a genuine root-cause gap in this repository, not a restatement of the original Solidity report.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or add an additional check invoked alongside it in `ChunkExecutorInner::verify_execution` and `db-tool`'s replay verifier) to compare `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` against the locally recomputed values before treating replay as verified, consistent with what `DoStateCheckpoint` already enforces on the live commit path.

### Proof of Concept
1. Archive/backup a range of mainnet transactions including checkpoint transactions where `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are set in the stored `TransactionInfo`.
2. Run `db-tool replay-on-archive verify` (or backup-restore with `verify_execution_mode`) against a node/build where the state/hot-state/position-state root computation differs from what produced the archive (e.g. a regression in the trading-native state root logic, or a corrupted position-state snapshot).
3. Observe that `verify_execution` at [8](#0-7)  calls `ensure_match_transaction_info`, which only checks status/gas/write-set-hash/event-root-hash — the divergent checkpoint hash is never compared — so `verify_execution` returns `Ok`, and the tool reports the archive as successfully replayed/verified despite the state-checkpoint divergence.

### Citations

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
        Ok(())
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-233)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

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
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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
```
