Found a genuine analog. The strongest local candidate is in `TransactionOutput::ensure_match_transaction_info`.

### Title
`ensure_match_transaction_info` skips verifying the `position_state_checkpoint_hash` (and state/hot-state checkpoint hashes), letting corrupted native-position roots pass replay/backup verification - ([File: types/src/transaction/mod.rs])

### Summary
`ensure_match_transaction_info` is the authoritative comparator used to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed/authenticated (e.g. during chunk replay verification and backup/restore verify tooling). It checks status, gas, write-set hash, and event root hash, but explicitly does **not** check the checkpoint hashes carried in `TransactionInfoV1` — including `position_state_checkpoint_hash`, which is the accumulator/JMT root for the new native-position subsystem gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Finding Description
The code contains its own acknowledgment of the gap: [1](#0-0) 

Meanwhile, `position_state_checkpoint_hash` is produced by two structurally different code paths that must independently reproduce the identical root:
1. At execution/checkpoint time: `DoStateCheckpoint::compute_position_checkpoint`, which extends the position tree per-checkpoint-boundary using `last_inner_checkpoint_index()`. [2](#0-1) 
2. At storage-commit time, when the execution-computed summary is absent (`chunk.position_state_summary` is `None`), `AptosDB::position_summary_at_commit` independently recomputes an equivalent summary from `chunk.transaction_outputs`, using its own checkpoint-boundary logic (`chunk.state.last_checkpoint().version()` filtered against the chunk range) and its own per-key coalescing loop. [3](#0-2) 

These two computations use different inputs/boundary logic (`last_inner_checkpoint_index()` vs. `chunk.state.last_checkpoint().version()`) and are only supposed to agree if `compute_trading_native_state_roots` is off in the second path (per the comment "not consensus-committed"). However, `ensure_match_transaction_info` — the only place that would catch a real divergence between what was authenticated on-chain (`txn_info.state_change_hash()`/checkpoint hashes) and what local replay recomputed — never inspects `position_state_checkpoint_hash` at all. So even if a bug is introduced in `compute_position_checkpoint` (or in `position_summary_at_commit`, or in the JMT/accumulator update code they call: `LedgerWithSummary::extend`, `merklize_position`), any replay-verification tooling built on this comparator (e.g. `db-tool replay-on-archive`, chunk-executor `verify_execution`) will report success while silently persisting/serving a wrong native-position root.

### Impact Explanation
This breaks the "authenticated API/state-view output bound to the wrong version/root" and "wrong accumulator root/Merkle proof accepted as valid" invariants for the native-position subsystem: state proofs served via `get_position_state_proof_by_version_ext` (`storage/aptosdb/src/db/aptosdb_reader.rs:106-133`) would be generated against a root that diverged from the correctly executed VM result, yet the divergence would not be flagged by the standard replay-verification path. In a hard-fork/replay/restore context this is exactly the class of bug the gate targets: committed state differing from the correct VM result without being caught by proof/replay verification.

### Likelihood Explanation
Medium: the gap is explicitly flagged by a `TODO` comment in the code itself, meaning the authors know it's incomplete, and it only manifests once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. Given the feature is still being built out (per the `features.move` comments describing it as covering "the native-position tree today" with more trees to follow), and given there are two independently-implemented root-computation code paths (`compute_position_checkpoint` vs `position_summary_at_commit`) with different checkpoint-boundary detection logic, the risk of an undetected divergence is real once the feature ships to mainnet with this comparator unchanged.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `TransactionInfoV1`) against the locally computed equivalents, and gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` from being enabled until this is done, per the code's own `TODO`.

### Proof of Concept
Not independently exploitable as a standalone PoC without the two divergence-triggering paths under test; the finding is a verification-gap proof, evidenced directly by the code and comment cited above (`types/src/transaction/mod.rs:2197-2203`) — replay-verify tooling calling `ensure_match_transaction_info` will return `Ok(())` for any `TransactionOutput` whose write set, status, gas, and event hashes match, regardless of whether `position_state_checkpoint_hash` in the corresponding `TransactionInfoV1` is correct.

**Uncertainty note:** I could not fully trace every caller of `ensure_match_transaction_info` within the available search budget to confirm it is reachable on the mainnet consensus-commit critical path versus only backup/replay tooling; this affects whether the impact is "hard-fork-only divergence during commit/replay/restore" (in scope) versus a lower-severity tooling gap. I was also unable to fully audit `LedgerWithSummary::extend`/`merklize_position` internals to confirm an actual arithmetic divergence between the two summary-computation paths — I found the structural verification gap and the two parallel computations, but not a concrete corrupted value in this pass.

### Citations

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L90-177)
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
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L382-469)
```rust
        // Advance the position pipeline (merklize + persist + advance the base).
        // Flag on: the summary comes from execution on the chunk; off: compute
        // it here so the tree still tracks forward (not consensus-committed).
        if let Some(store) = bundle.state_store.as_ref() {
            let new_state = match chunk.position_state_summary {
                Some(summary) => summary.clone(),
                None => self.position_summary_at_commit(chunk)?,
            };
            let estimated_items = chunk.transaction_outputs.len();
            let mut bufstate = store.buffered_state_locked();
            bufstate.update(
                new_state,
                (),
                estimated_items,
                sync_commit || chunk.is_reconfig,
            )?;
        }
        Ok(())
    }

    /// Computes the position summary at commit time by extending the in-memory
    /// tip with this chunk's position writes, freezing on the persisted base.
    /// Used only when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is off (otherwise the
    /// summary comes from execution on the chunk).
    fn position_summary_at_commit(
        &self,
        chunk: &ChunkToCommit,
    ) -> Result<PositionLedgerStateWithSummary> {
        let bundle = self
            .position
            .as_ref()
            .expect("called only when position is present");
        let store = bundle
            .state_store
            .as_ref()
            .expect("called only when state_store is present");
        let persisted_base = bundle
            .persisted
            .as_ref()
            .expect("persisted present when state_store is")
            .get();

        let (mut latest, mut last_checkpoint) = {
            let state = store.current_state();
            let current = state.lock();
            (current.latest().clone(), current.last_checkpoint().clone())
        };

        let chunk_first = chunk.first_version;
        let chunk_last_inclusive = chunk_first + chunk.transaction_outputs.len() as Version - 1;
        let checkpoint_within_chunk = chunk
            .state
            .last_checkpoint()
            .version()
            .filter(|v| (chunk_first..=chunk_last_inclusive).contains(v));

        let mut pending: HashMap<HashValue, PositionSlot> = HashMap::new();
        let extend_on_base = |latest: &PositionStateWithSummary,
                              version: Version,
                              updates: Vec<(HashValue, PositionSlot)>|
         -> Result<PositionStateWithSummary> {
            let proof_reader = PositionProofReader {
                merkle_db: bundle.merkle_db.clone(),
                version: persisted_base.version(),
            };
            latest.extend(version, updates, persisted_base.summary(), &proof_reader)
        };

        for (i, output) in chunk.transaction_outputs.iter().enumerate() {
            let version = chunk_first + i as Version;
            for (key, op) in output.write_set().native_position_iter() {
                let value_hash = op.as_write_op().as_state_value_opt().map(CryptoHash::hash);
                pending.insert(key.hash(), PositionSlot {
                    state_key: key.clone(),
                    value_hash,
                    value: None,
                });
            }
            if Some(version) == checkpoint_within_chunk && !pending.is_empty() {
                let updates: Vec<_> = std::mem::take(&mut pending).into_iter().collect();
                latest = extend_on_base(&latest, version, updates)?;
                last_checkpoint = latest.clone();
            }
        }
        if !pending.is_empty() {
            let updates: Vec<_> = pending.into_iter().collect();
            latest = extend_on_base(&latest, chunk_last_inclusive, updates)?;
        }
```
