### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting replay-verify accept locally-computed native-position/hot-state roots that diverge from the authenticated ledger - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by the chunk executor's `verify_execution` path (exercised by db-tool's replay-verify and by output-based transaction restore/replay) to check that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` from a backup/proof. The function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO`.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates only status, gas, write-set hash, and event-root hash of a re-executed `TransactionOutput` against a `TransactionInfo`. The trailing comment makes the gap explicit: [2](#0-1) 

This means the state-checkpoint hash, hot-state-checkpoint hash, and (newly introduced in this fork) `position_state_checkpoint_hash` fields of `TransactionInfoV1` are never cross-checked against locally recomputed roots by this function, even though `TransactionInfoV1` now carries a `position_state_checkpoint_hash` field bound to the native-position Jellyfish Merkle tree introduced by this fork (`COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `NATIVE_POSITION` features, see [3](#0-2)  and the computation in [4](#0-3) ).

This function is used in `verify_execution` in the chunk executor: [5](#0-4) , which re-executes transactions and compares outputs against `transaction_infos`/`write_sets`/`event_vecs` supplied from a backup or database being verified — this is the code path used by `storage/db-tool/src/replay_verify.rs` and the backup transaction-restore/verify flow (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`).

Because the check skips the checkpoint-hash family, if the locally recomputed native-position (or hot-state) root diverges from what's embedded in the authenticated `TransactionInfoV1` (e.g., due to a bug in `compute_position_checkpoint`, in the native-position JMT commit path, or in the backup/restore replay of position writes), `verify_execution`/replay-verify will report success even though the position state root — a value bound into the transaction accumulator and thus into consensus-signed `LedgerInfo` — is wrong.

### Impact Explanation
This does not itself corrupt live consensus commits — the full-hash check on the true commit path (`ensure_transaction_infos_match` in `chunk_result_verifier.rs`, used for state-sync) compares the entire serialized `TransactionInfo` (including `position_state_checkpoint_hash`) hash-for-hash, so the normal state-sync commit path is not affected. The impact is scoped to **replay-verify tooling** (`db-tool replay-verify`) and any output-based restore/replay flow that specifically relies on `ensure_match_transaction_info` as its correctness oracle: a corrupted or diverging position-state root can pass `replay_verify` as "correct" even though the authenticated ledger commits a position root that the verifying node's own execution disagrees with. This weakens an integrity tool meant to catch exactly this class of divergence (hard-fork-only silent state divergence), and is explicitly called out by the code author as a known gap ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`").

### Likelihood Explanation
Low likelihood as a standalone mainnet-exploitable bug: `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `NATIVE_POSITION` are new, gated features in this fork, and the primary commit-time defense (full `TransactionInfo` hash equality in `ensure_transaction_infos_match`) is unaffected. The bug's practical effect is that replay-verify/db-tool auditing silently loses coverage for the checkpoint-hash family, which could mask a real, separate bug in position/hot-state root computation from being caught by the standard verification tooling once the feature is turned on.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` when present/expected (as the existing TODO instructs), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` are enabled on any network relying on `verify_execution`/replay-verify as an integrity check.

### Proof of Concept
Not applicable as a live exploit — this is a tooling/verification-gap finding, not a directly triggerable state-corruption path. Concretely reproducible via unit test: construct a `TransactionOutput`/`TransactionInfoV1` pair whose `write_set`, `events`, `gas_used`, and `status` match but whose `position_state_checkpoint_hash` differs, and observe `ensure_match_transaction_info` returns `Ok(())` at [1](#0-0) .

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L86-190)
```rust
    /// Computes the position summary (latest + last_checkpoint) and per-txn
    /// position root for this chunk by extending the parent on the persisted
    /// base. The root depends only on the position writes, not on the base, so
    /// it's deterministic across nodes.
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
