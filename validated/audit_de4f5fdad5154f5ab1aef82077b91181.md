I found a confirmed local root cause distinct from the external report: a self-acknowledged verification gap in replay-verify tooling's per-transaction integrity check.

### Title
Replay-verify's `ensure_match_transaction_info` skips state/hot-state/position checkpoint hash comparison, allowing corrupted checkpoint state to pass verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction comparator used by `storage/db-tool/src/replay_on_archive.rs` and by `execution/executor/src/chunk_executor/mod.rs::verify_execution` (backup replay-verify path) to confirm that a locally re-executed `TransactionOutput` matches the trusted, ledger-committed `TransactionInfo`. This function checks status, gas, write-set hash, and event root hash, but explicitly and admittedly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.

### Finding Description [1](#0-0)  shows `ensure_match_transaction_info` validating `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`, then returning `Ok(())` without ever touching the checkpoint-hash fields. The code contains its own acknowledgment of the gap: [2](#0-1) 
> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked as the terminal correctness check in two important tools:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical blocks against an archive DB and calls `ensure_match_transaction_info` per transaction: [3](#0-2) 
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used by backup replay-verify (`replay_verify.rs` / `ReplayVerifyCoordinator`), which similarly calls it per-transaction after re-execution: [4](#0-3) 

`TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — these are the fields that bind the accumulator-committed transaction info to the state Merkle root (main state, hot state, and the new native-position tree), as seen in the accessors [5](#0-4) . Because `ensure_match_transaction_info` never compares these fields against locally recomputed checkpoint hashes, a divergence between the archived/committed `TransactionInfo` and what local re-execution would actually produce for state/hot-state/position roots is never detected by this comparator.

Separately, the "normal" state-sync/chunk-execution ledger-update path (`DoStateCheckpoint::run` with `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints`, seen at [6](#0-5) ) does validate these checkpoint hashes against the recomputed accumulator/state roots. So the gap is specific to tools relying solely on `ensure_match_transaction_info` as their correctness oracle — i.e., `replay_on_archive` and the `verify_execution` path in `chunk_executor` used for archive replay-verification — not the live commit path.

### Impact Explanation
Replay-verify is the primary tool operators and auditors use to confirm that a locally re-executed history reproduces the exact, consensus-committed ledger state (main state root, hot-state root, and — once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled — the native-position state root). Because the checkpoint-hash fields are skipped, `replay_on_archive`/chunk-executor's `verify_execution` can report a clean, successful replay even though the locally computed state checkpoint root (and, notably, the authenticated native-position state root) silently diverges from what is committed on-chain. This defeats the purpose of replay-verify as an integrity check for state commitment, and could mask a real state-divergence bug (e.g., in the new native-position/hot-state subsystems) that would otherwise indicate consensus-critical non-determinism or a corrupted ledger state going undetected until much later.

### Likelihood Explanation
This is not attacker-triggered in the traditional sense (no external adversary is required); it is a tooling/verification-completeness gap that is deterministically present any time `replay_on_archive` or the chunk-executor `verify_execution` path is used to check historical state or trading-native roots. The comment in the code itself flags this as a known, outstanding gap to close "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — meaning the risk is acknowledged as live and unresolved in the current code, and would silently manifest as soon as any bug in state-checkpoint/hot-state/position-root computation is introduced elsewhere (e.g., in the new native-position replay code such as `replay_position_after_snapshot`), since replay-verify would not catch it.

### Recommendation
Extend `ensure_match_transaction_info` to also compare locally-computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info` and computable from the replay context) against the values in the trusted `TransactionInfo`, and fail loudly on mismatch, exactly as `DoStateCheckpoint::run`'s `known_*_checkpoints` mechanism already does for the live commit/chunk-execution path. This should be a mandatory pre-condition before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as the code comment itself states.

### Proof of Concept
Not directly exploitable as a state-corruption PoC by an external attacker; the "proof" is the code path itself:
1. `replay_on_archive`'s `execute_and_verify` re-executes a chunk and calls `executed_outputs[idx].ensure_match_transaction_info(...)` [7](#0-6) .
2. Suppose local re-execution computes a different `state_checkpoint_hash` (or `position_state_checkpoint_hash`) than the one recorded in the archived `TransactionInfo` — e.g., due to a bug in `StateStore`/`PositionStateStore` update logic, or in the native-position replay path such as `replay_position_after_snapshot` in `storage/aptosdb/src/db/aptosdb_native_position.rs`.
3. Because `ensure_match_transaction_info` never inspects `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, this divergence produces no error; `execute_and_verify` returns `Ok(None)` and the chunk is reported as verified.
4. Operators relying on `replay_on_archive` therefore get a false assurance that the state (or native-position) root is correct, when it is not.

**Uncertainty**: I could not fully trace whether every call site that needs checkpoint verification (e.g., all consumers of `verify_execution_mode` outside the two call sites found) has an independent, redundant check of checkpoint hashes elsewhere in the pipeline; if such a redundant check exists specifically for `replay_on_archive`'s CLI tool, the practical exploitability would be reduced to the chunk-executor `verify_execution` path only. This would require deeper tracing of `VerifyExecutionMode` consumers than was feasible in this session.

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

**File:** types/src/transaction/mod.rs (L2352-2364)
```rust
    pub fn hot_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.hot_state_checkpoint_hash,
        }
    }

    pub fn position_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.position_state_checkpoint_hash,
        }
    }
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

**File:** execution/executor/src/chunk_executor/mod.rs (L374-413)
```rust
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
