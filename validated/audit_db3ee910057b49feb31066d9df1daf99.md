## Finding

### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting replay-verification silently accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the authenticated-commitment check used by replay/verification tooling (`db-tool replay-on-archive`, `aptos-debugger`, `cli`) to confirm that a freshly re-executed transaction matches the previously committed `TransactionInfo` for a given version. The function validates status, gas used, the write-set hash (`state_change_hash`), and the event-accumulator root (`event_root_hash`), but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the freshly computed roots — these fields are simply not touched before returning `Ok(())`.

### Finding Description
`ensure_match_transaction_info` hashes and checks the write set and events, but ends with a bare `Ok(())` and an explicit acknowledgement that the checkpoint hashes are ignored: [1](#0-0) 

The `TransactionInfo`/`TransactionInfoV1` structures carry `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — these are the Sparse-Merkle/Jellyfish-Merkle roots binding the authenticated global state (including the new "trading-native" position state tree) at that version: [2](#0-1) 

This function is the sole commitment check used by `replay_on_archive`'s `execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` against the `expected_txn_info` pulled from the backup/archive to decide whether replay "passed": [3](#0-2) 

Because the state/hot-state/position-state checkpoint hashes are never recomputed and compared here, any divergence between the locally re-executed state root and the archived/authenticated root (e.g. from a JMT/position-state bug in `do_state_checkpoint.rs`'s `compute_position_checkpoint`, a hard-fork-only execution divergence, or corrupted archive data) is invisible to this check. `do_state_checkpoint.rs` shows how much surface area feeds into these checkpoint hashes — parent/persisted state summaries, hot-state updates, and the new `position_state_summary`/`compute_trading_native_state_roots` path — all of which are excluded from the verification: [4](#0-3) 

### Impact Explanation
Replay/verification tooling is one of the few authenticated cross-checks that a corrupted or hard-fork-divergent state root was actually committed correctly. If `ensure_match_transaction_info` returns success while the state/hot-state/position-state root actually diverges, an operator relying on `replay-on-archive` (or `aptos-debugger`/`cli` calling the same function) would get a false "replay succeeded" signal even though the locally computed state commitment differs from the one recorded on-chain. This masks state-integrity regressions (including those introduced by bugs in the state-checkpoint/position-state-tree code) exactly in the class of "Hard-fork-only divergence during commit, replay, restore, or proof verification" that this analysis is scoped to catch.

### Likelihood Explanation
The gap is deterministic and unconditional — it is not a corner case, it's the normal code path every time `ensure_match_transaction_info` is invoked, and the code itself contains a `TODO(trading-native)` comment acknowledging the omission: [5](#0-4) 
Any actual state-root divergence (from a bug elsewhere, e.g. in position-state tree computation) would go undetected whenever this is the verification mechanism relied upon.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the actual roots produced by the checkpoint/summary computation, failing the check (as it already does for `state_change_hash`/`event_root_hash`) on mismatch, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production verification flows.

### Proof of Concept
1. Run `db-tool replay-on-archive` (or `aptos-debugger`) against an archive/backup where a corrupted or divergent state/position-state root was recorded in `TransactionInfo` for some version (e.g. due to an unrelated bug in `compute_position_checkpoint`).
2. `execute_and_verify` re-executes the transaction and calls `ensure_match_transaction_info` with the expected `TransactionInfo`.
3. Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are never compared, the call returns `Ok(())` even though the freshly computed checkpoint roots (from `DoStateCheckpoint::run`) differ from those recorded, so the tool reports the transaction/chunk as verified when it is not.

### Citations

**File:** types/src/transaction/mod.rs (L2168-2203)
```rust
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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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
