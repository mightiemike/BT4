## Finding: State-checkpoint/root-hash fields are never verified by `ensure_match_transaction_info`, letting corrupted state roots pass replay/execution verification

### Title
Replay-verification and chunk-executor verify-execution accept transactions whose committed state root diverges from the correct VM result - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by every "does my re-executed/restored output match the trusted, previously-committed `TransactionInfo`" check in the codebase: the archive replay-verify audit tool, the chunk executor's execute-and-verify mode used during backup restore, and the CLI/debugger transaction-replay tooling. This function checks status, gas used, write-set hash, and event root hash, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the resulting world-state root that gets bound into the transaction-accumulator/ledger. A divergence in state-tree computation (JMT update bug, hot-state root bug, or the not-yet-enabled native-position state root) that still produces a byte-identical write set will be reported as a successful, verified replay even though the actual committed state root is wrong.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  compares only `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash`. The function's own trailing comment acknowledges part of the gap: [2](#0-1) 

but the acknowledged gap only calls out the new hot-state/position-state fields; the base `state_checkpoint_hash` (present since `TransactionInfoV0`, the SMT root of world state after the transaction) is *also* never compared, and this omission is not called out at all. `state_checkpoint_hash` is not a pure function of this transaction's own `write_set` — it depends on the entire prior state tree — so a correct write set can still yield a wrong state root if the state-tree merge/update path (`storage/aptosdb`, jellyfish-merkle restore, hot-state summary update, or the new position-state summary in `execution/executor/src/workflow/do_state_checkpoint.rs`) is buggy.

This comparator is relied upon, with no independent state-root check, in three real code paths:
- `storage/db-tool/src/replay_on_archive.rs`, the tool operators use to audit that archived history replays deterministically: [3](#0-2) 
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used during backup/fast-sync "verify execution" mode to confirm a restored chunk's outputs match the trusted `TransactionInfo` before the chunk is accepted: [4](#0-3) 
- CLI/debugger transaction-replay commands: [5](#0-4) , [6](#0-5) 

None of these call sites separately recompute and compare the state-checkpoint/hot-state/position-state hash against the trusted `TransactionInfo` before declaring "match".

### Impact Explanation
This breaks the state-commitment/proof-integrity guarantee that these tools exist to enforce: "recomputed output must equal the authenticated, previously-committed result." If state-tree computation diverges from the canonical result (e.g., a bug in the hot-state summary update, the new native-position SMT path added by `DoStateCheckpoint::compute_position_checkpoint` at [7](#0-6) , a JMT restore inconsistency, or storage corruption during restore), a node or auditor running `replay_on_archive` or backup-restore verify-execution will still report success, because the very field that would reveal the divergence (`state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is skipped. This is a hard-fork/divergence-class integrity gap: wrong state roots can be accepted as "verified" during restore or audited as "correct" during replay, silently masking committed-state corruption or a state-tree computation bug that differs from the canonical VM result.

### Likelihood Explanation
The gap is unconditional in the current code — it applies on every call to `ensure_match_transaction_info`, in every configuration, not only once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled as the comment implies. Because these tools are the primary safety net for detecting state-tree bugs during backup restore and full-chain replay audits, any independent bug in the state-tree/hot-state/position-state computation path would go undetected specifically by the mechanism meant to catch it.

### Recommendation
Extend `ensure_match_transaction_info` to also recompute/verify `state_checkpoint_hash` whenever a state-checkpoint boundary is known, and to compare `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` when present in the trusted `TransactionInfo`, failing the ensure! with a clear mismatch message analogous to the existing write-set/event checks, rather than silently returning `Ok(())`.

### Proof of Concept
Conceptual repro (cannot be executed here, no sandbox access):
1. Introduce (or trigger via existing bug) a divergence between the recomputed world-state root and the trusted `state_checkpoint_hash` in a `TransactionInfo` while keeping the write set byte-identical (e.g., corrupt/replay against a state-tree with a stale hot-state summary, or a bug in `compute_position_checkpoint`).
2. Run `storage/db-tool/src/replay_on_archive.rs` (or the chunk executor's `verify_execution` during a backup restore with `verify_execution_mode` enabled) over that version range.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` because it only checks status/gas/write-set-hash/event-root-hash, never `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, so the tool reports the replay as verified/successful despite the actual committed state root being wrong.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
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
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L90-190)
```rust
    fn compute_position_checkpoint(
        execution_output: &ExecutionOutput,
        parent: Option<&LedgerWithSummary<PositionStateWithSummary>>,
        persisted: &ProvablePositionStateSummary,
        known_position_state_checkpoints: Option<Vec<Option<HashValue>>>,
    ) -> Result<(
        LedgerWithSummary<PositionStateWithSummary>,
        Vec<Option<HashValue>>,
    )> {
        let _timer = OTHER_TIMERS.timer_with(&["get_position_checkpoint_hashes"]);

        let num_txns = execution_output.to_commit.len();
        let first_version = execution_output.first_version;
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();
        let base_summary = persisted.summary();
        // No in-memory parent at genesis / first block after enabling: seed
        // from the pre-committed position tip (covers committed writes the
        // merklized snapshot may lag).
        let parent_latest =
            parent.map_or_else(|| persisted.base().latest().clone(), |p| p.latest().clone());
        let parent_last_checkpoint = parent.map_or_else(
            || persisted.base().last_checkpoint().clone(),
            |p| p.last_checkpoint().clone(),
        );

        // Empty chunk: nothing to extend (avoids the `num_txns - 1` underflow).
        if num_txns == 0 {
            let summary = LedgerWithSummary::from_latest_and_last_checkpoint(
                parent_latest,
                parent_last_checkpoint,
            );
            return Ok((summary, vec![]));
        }

        // Collapse position writes (latest-per-key) over a version range into
        // SMT leaf updates.
        let collect = |range: std::ops::Range<usize>| -> Vec<(HashValue, PositionSlot)> {
            let mut latest: HashMap<HashValue, PositionSlot> = HashMap::new();
            for i in range {
                for (key, op) in execution_output.to_commit.transaction_outputs[i]
                    .write_set()
                    .native_position_iter()
                {
                    let value_hash = op.as_write_op().as_state_value_opt().map(StateValue::hash);
                    latest.insert(key.hash(), PositionSlot {
                        state_key: key.clone(),
                        value_hash,
                        value: None,
                    });
                }
            }
            latest.into_iter().collect()
        };

        let (new_latest, new_last_checkpoint) = if let Some(ci) = last_checkpoint_index {
            let checkpoint_version = first_version + ci as u64;
            let new_ckpt = parent_latest.extend(
                checkpoint_version,
                collect(0..ci + 1),
                base_summary,
                persisted,
            )?;
            if ci + 1 == num_txns {
                (new_ckpt.clone(), new_ckpt)
            } else {
                let last_version = first_version + num_txns as u64 - 1;
                let new_latest = new_ckpt.extend(
                    last_version,
                    collect(ci + 1..num_txns),
                    base_summary,
                    persisted,
                )?;
                (new_latest, new_ckpt)
            }
        } else {
            // No checkpoint in this chunk: only the latest advances.
            let last_version = first_version + num_txns as u64 - 1;
            let new_latest = parent_latest.extend(
                last_version,
                collect(0..num_txns),
                base_summary,
                persisted,
            )?;
            (new_latest, parent_last_checkpoint)
        };

        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
    }
```
