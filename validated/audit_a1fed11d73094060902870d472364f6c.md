### Title
`ensure_match_transaction_info` skips authenticated checkpoint-hash fields, letting replay-verify tooling accept a divergent trading-native state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verify tooling (`storage/db-tool/src/replay_on_archive.rs`, backup-cli replay-verify, and the chunk executor's replay verifier) to confirm that locally re-executed output matches the authenticated `TransactionInfo` fetched from a backup/archive. It checks status, gas used, write-set hash, and event-root hash, but deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and the newly added `position_state_checkpoint_hash` fields — exactly the fields that carry the authenticated state/Merkle roots, including the native-trading position state root.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` compares transaction status, gas used, `write_set_hash`, and `event_root_hash` against the supplied `TransactionInfo`, but explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`: [1](#0-0) [2](#0-1) 

The code itself contains an explicit TODO acknowledging the gap: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."*

This function is the sole verification gate used in `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which drives a chain-wide replay/verify workflow: [3](#0-2) 

The `TransactionInfoV1` struct persists `position_state_checkpoint_hash` as an authenticated field, produced during execution by `DoStateCheckpoint::compute_position_checkpoint` and gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature: [4](#0-3) [5](#0-4) 

The state root itself is a Merkle root over the native-position store (`PositionStateWithSummary`, computed via `LedgerWithSummary`/JMT extend logic in `storage/aptosdb/src/db/aptosdb_writer.rs` and `aptosdb_native_position.rs`), separate from the ordinary account state tree. Because `ensure_match_transaction_info` never checks this hash (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`), any local divergence in the position-state (or main-state, or hot-state) root — from a bug in `compute_position_checkpoint`, `PositionStateStore::extend`, replay-order handling in `replay_position_after_snapshot`, or any other bug in the position/state-root computation path — will not be detected by this comparator. Replay tooling will report success (no error surfaced from `execute_and_verify`) even though the locally reconstructed and committed ledger state (and its Merkle root) is not the one that was actually consensus-committed.

### Impact Explanation
This breaks the "authenticated API/proof output bound to correct version/root" invariant for archival replay-verification: a wrong `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — i.e., a wrong state root — will not be flagged as a mismatch by the tool whose entire purpose is to catch exactly this class of divergence. On mainnet, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`TRANSACTION_INFO_V1` are enabled, this means the operational safety net (replay-verify used to validate archive/backup integrity and catch nondeterminism/consensus-vs-execution divergence) silently passes over state-root corruption in the position-native (perpetuals/trading) subsystem as well as the primary state tree, undermining confidence in restored/replayed data used for audits, disaster recovery, and dispute resolution. This is a genuine gap in a proof/commitment-integrity invariant, though it is a detection/verification gap rather than a bug that itself corrupts consensus-committed state — the divergent root is still what full nodes verify during normal consensus/execution (assuming other checks there are intact); this tool is a secondary, offline verification path.

### Likelihood Explanation
The code comment confirms this is a real, currently-active gap (not a hypothetical): the TODO exists precisely because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is being introduced and the author flagged that the checkpoint-hash validation must be added "before enabling" the feature. Given the newness and complexity of the native-position/state-summary pipeline (custom JMT-like structure, chunked commit, replay-after-snapshot reconciliation), the likelihood of an actual root-computation divergence occurring is non-trivial, and if it occurs, this comparator will not catch it in `replay_on_archive`/replay-verify flows.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when present), and `position_state_checkpoint_hash` (when present) between the locally computed `TransactionInfo` and the expected one, as the existing TODO comment recommends, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled in production.

### Proof of Concept
Not applicable as a runnable exploit — this is a verification-logic gap rather than an exploitable state-corruption primitive. Demonstration path: enable `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, introduce (or trigger via an existing latent bug in) a discrepancy between the locally recomputed `position_state_checkpoint_hash` and the archived one (e.g. by altering the order/coalescing behavior in `NativeStateCommitter::apply` or `replay_position_after_snapshot`), then run `storage/db-tool/src/replay_on_archive.rs`'s verifier over that version range — `execute_and_verify` will report no error because `ensure_match_transaction_info` never inspects the mismatched field.

### Citations

**File:** types/src/transaction/mod.rs (L2159-2178)
```rust
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
```

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-83)
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

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```
