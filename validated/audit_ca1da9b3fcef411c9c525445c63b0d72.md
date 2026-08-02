### Title
Replay-verification accepts a divergent state root because `TransactionOutput::ensure_match_transaction_info` never checks the state/hot-state/position checkpoint hashes - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity gate used by the archive replay-verification tool (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transaction outputs match the authenticated `TransactionInfo` recorded on-chain. The function checks status, gas used, write-set hash, and event root hash, but it explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that bind a transaction's checkpoint to the correct Merkle/JMT state root. This is acknowledged in-code by a `TODO(trading-native)` comment, and the gap is real, unpatched code, not a hypothetical.

### Finding Description
`ensure_match_transaction_info` is defined at: [1](#0-0) 

It verifies:
- `status()` matches the `TransactionInfo`'s status
- `gas_used()` matches
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) matches `txn_info.state_change_hash()`
- `event_root_hash` matches `txn_info.event_root_hash()`

But it never touches `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (V1), or `txn_info.position_state_checkpoint_hash()` (V1). The comment right before the function returns `Ok(())` states this directly: [2](#0-1) 

These checkpoint hashes are exactly the values that bind a state-checkpoint transaction to the correct global state root, hot-state root, and (when the `compute_trading_native_state_roots` feature is on) the "native position" state root — computed in `DoStateCheckpoint::run` / `compute_position_checkpoint`: [3](#0-2) [4](#0-3) 

The only production caller of `ensure_match_transaction_info` is the archive-replay verification tool: [5](#0-4) 

That tool's entire purpose is to re-execute historical transactions and detect divergence from the authenticated ledger (a hard-fork / consensus-bug detector run against archive nodes). Because the checkpoint-hash fields are never compared, a locally computed state root, hot-state root, or position-state root that diverges from the authenticated one at a state-checkpoint boundary will not cause `execute_and_verify` to flag an error — the loop will report success (`Ok(None)`) and move on to the next chunk, silently accepting a state-root mismatch as "verified."

This is structurally the same defect class as the seed report: a downstream consumer performs a state-transition-ending check (liquidate / accept-as-verified) while skipping one of the fields required to fully validate the underlying invariant (loan duration / checkpoint hash), causing premature/incorrect acceptance.

### Impact Explanation
Replay-verify is one of the primary tools operators and the Aptos Labs backup/verification pipeline use to detect state divergence (i.e., a silent hard fork or a VM/state-computation bug) on archive nodes. Because `ensure_match_transaction_info` does not check `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, a divergence introduced by a bug in the JMT/hot-state/position-state computation path could go completely undetected by this tool, even though write-set and event hashes for individual transactions are still correctly checked. This falls squarely under the in-scope category "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong accumulator root ... or state proof accepted as valid" — here, a wrong state-checkpoint root is accepted as valid by the verification harness. This does not corrupt live consensus-committed state by itself, but it defeats the detection mechanism relied upon to catch such corruption, which is a proof/replay-integrity break with potentially critical downstream consequences (an undetected fork could persist and compound).

### Likelihood Explanation
The gap is deterministic and always present — it is not a race condition or timing issue. Any divergence localized to a state-checkpoint transaction (rather than a within-block, non-checkpoint transaction) that does not also change the write set or events reported for that same transaction will bypass detection. The comment in the code confirms the authors are aware this must be fixed before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` fully, indicating the position-state-root case is a known, currently-live blind spot as that feature is rolled out.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived checkpoint roots (when available in-context) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` before returning `Ok(())`, or otherwise thread the locally-computed checkpoint hashes into this verification path so replay-verify tooling cannot report success while an authenticated checkpoint root diverges from local re-execution. This should be resolved before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as flagged in the existing TODO.

### Proof of Concept
Conceptual (no fabricated exploit code, based on direct code reading):
1. Assume a bug (or malicious validator set collusion at genesis/fork) causes the locally-recomputed state root at a checkpoint transaction to diverge from the authenticated `state_checkpoint_hash`/`position_state_checkpoint_hash` in the backed-up `TransactionInfo`, while the write set and events for that specific transaction remain byte-identical (state-checkpoint transactions typically carry an empty/synthetic write set and no user events).
2. Run `db-tool replay-on-archive` over the affected version range.
3. In `execute_and_verify` (`storage/db-tool/src/replay_on_archive.rs:392`), `ensure_match_transaction_info` is called and returns `Ok(())` because status/gas/write-set-hash/event-root all still match.
4. The tool reports the range as fully verified with no errors, despite the state (or trading-native position) root having diverged from the correct chain state — a hard-fork-class divergence goes undetected by the tool whose job is to detect exactly that.

Note: I was not able to fully trace whether any other authenticated code path (outside `db-tool`) also calls `ensure_match_transaction_info` and depends on this same check for security-critical state acceptance; my search only found the two call sites in `aptos-move/cli/src/commands.rs` (not examined in depth) and `storage/db-tool/src/replay_on_archive.rs`. If `aptos-move/cli/src/commands.rs` uses this in a security-relevant local-simulation-verification context, the impact could be broader than described here.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-83)
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

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L90-189)
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
