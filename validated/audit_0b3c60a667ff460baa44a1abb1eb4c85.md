## Title
Replay-verify tooling silently ignores position-state (and hot-state) checkpoint hash divergence - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole gate `replay_on_archive`'s `Verifier::execute_and_verify` uses to decide whether a re-executed transaction output matches the transaction info recorded in an authenticated backup/archive. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` validates transaction status, gas, write-set hash and event root hash, but the function ends with an explicit acknowledgement that checkpoint hashes are unchecked: [1](#0-0) 

This means any divergence in the state checkpoint roots computed by `DoStateCheckpoint::run` / `compute_position_checkpoint` (in `execution/executor/src/workflow/do_state_checkpoint.rs`) relative to the checkpoint hash embedded in a stored/backed-up `TransactionInfo` will not be detected by this comparator. The position-state root is derived purely from `native_position_iter()` write-set entries controlled by transaction execution (trading-native object writes), and is folded into a `LedgerWithSummary<PositionStateWithSummary>` Merkle structure that is only ever asserted equal via `get_state_checkpoint_hashes` at construction time (against `known_position_state_checkpoints` sourced from locally-computed txn infos in the normal execution path), never re-validated against an *externally-provided* `TransactionInfo` in `ensure_match_transaction_info`: [2](#0-1) [3](#0-2) 

The consumer of this comparator that matters for the "authenticated response" scope is `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which calls `ensure_match_transaction_info` on re-executed outputs against `expected_txn_infos` pulled from a backup: [4](#0-3) 

Because the comparator never inspects `position_state_checkpoint_hash`, a divergence between the position Merkle root actually computed during re-execution and the `position_state_checkpoint_hash` stored in the backed-up `TransactionInfoV1` will pass silently — the replay tool will report success even though the authenticated position-state root diverges from local execution.

### Impact Explanation
This is a proof-integrity / authenticated-response-binding gap in the ledger's own tooling: the `replay_on_archive` verifier (and other callers using `ensure_match_transaction_info`, e.g. `aptos-debugger` and CLI commands) is expected to catch any divergence between locally-computed state and the values embedded in a backup's `TransactionInfo`. Because the checkpoint-hash fields are excluded from the comparison, a corrupted, buggy, or maliciously-tampered `position_state_checkpoint_hash` in a backup/archive (or a client-side bug in `compute_position_checkpoint`) would not be caught by replay-verify, undermining confidence that "replay-verify passed" implies the position state root is authentic. This matches the "corrupt proof material / misbind an authenticated response" category in scope, scoped specifically to the position-state checkpoint hash (and, as the comment notes, also state/hot-state checkpoint hashes in this particular comparator, though those are otherwise checked via a separate summary-equality mechanism during normal execution — this gap is specific to the external-verification path).

### Likelihood Explanation
The gap is unconditional whenever `ensure_match_transaction_info` is used to validate transaction outputs against an externally supplied `TransactionInfo` (the backup/restore/replay path), and requires no special validator collusion — it's a straightforward comparator omission acknowledged in the code itself via the `TODO(trading-native)` comment. However, actual exploitability depends on the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature/`compute_trading_native_state_roots` flag being enabled and on an attacker being able to influence what `TransactionInfo` is fed into `execute_and_verify` (e.g., via a compromised or malicious backup source), which is a scenario partly outside the "unprivileged transaction/API path" framing of the question — I could not fully verify from the indexed files whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently enabled on mainnet, since that determination depends on runtime on-chain feature flag state not visible in the static code (`types/src/on_chain_config/aptos_features.rs` only defines the flag).

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on `txn_info`) against locally-computed values before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, as the TODO itself instructs.

### Proof of Concept
Confirmed by direct code inspection rather than a runnable test (no execution environment available here):
1. `DoStateCheckpoint::compute_position_checkpoint` computes the true position root from `write_set().native_position_iter()` entries and folds it into `position_state_checkpoint_hashes` [5](#0-4) .
2. A `TransactionInfo` (e.g. from a hostile/faulty backup) can carry an arbitrary `position_state_checkpoint_hash` value.
3. `TransactionOutput::ensure_match_transaction_info` (lines 2139-2204 of `types/src/transaction/mod.rs`) never reads `txn_info`'s checkpoint-hash fields at all in its `ensure!` checks, so it returns `Ok(())` regardless of whether the position root matches — as explicitly documented by the `TODO(trading-native)` comment at lines 2197-2202.
4. `replay_on_archive::Verifier::execute_and_verify` treats `Ok(())` from this call as "verified", so a replay-verify run over a range containing corrupted `position_state_checkpoint_hash` values in the input `TransactionInfo`s would report success.

**Caveat:** This is a documented, self-acknowledged gap in code (the `TODO(trading-native)` comment), not a hidden exploit path I discovered independently. I was not able to confirm the mainnet activation status of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/trading-native feature from the static codebase, nor fully trace whether replay-verify's `TransactionInfo` inputs can be attacker-controlled in a genuinely "unprivileged" way (as opposed to requiring a compromised backup source) — both would need to be confirmed in a live/administered environment or via a Devin session with the full repo and feature-flag/genesis state.

### Citations

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-75)
```rust
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
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L90-106)
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
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L129-145)
```rust
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

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
        }
```
