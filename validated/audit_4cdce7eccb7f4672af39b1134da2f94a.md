## Title
`ensure_match_transaction_info` skips checkpoint-hash validation, letting `db-tool replay-verify` accept a divergent state root as valid — ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info()` (used by `storage/db-tool/src/replay_on_archive.rs`'s replay-verify tool, `aptos-move/aptos-debugger`, and the CLI's replay path) is the authoritative check that a locally re-executed transaction output matches the archived/authenticated `TransactionInfo` for that version. The function explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the exact fields that commit the JMT/state root to the ledger. This means replay-verify can report "success" even when the locally computed state root diverges from the authenticated on-chain state root.

### Finding Description
`ensure_match_transaction_info` validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but has a self-documented gap: [1](#0-0) 

```rust
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
Ok(())
```

This function is invoked directly by the real replay-verify tool at `storage/db-tool/src/replay_on_archive.rs`, where a locally executed `TransactionOutput` is compared against the `expected_txn_infos[idx]` loaded from the archived, ledger-info-authenticated backup: [2](#0-1) 

The `TransactionInfo` (and its `TransactionInfoV1` variant) carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class, hash-committed fields of the accumulator leaf: [3](#0-2) 

Because `ensure_match_transaction_info` never re-derives and compares the locally computed state checkpoint root(s) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, a scenario where the VM/state-computation logic (or the trading-native "position" subsystem introduced by these repos, e.g. `storage/aptosdb/src/db/aptosdb_native_position.rs`) produces a different state tree than what was originally committed will not be caught by this check. Only write-set hash, events, gas, and status are validated — none of which independently guarantee the resulting Sparse-Merkle/JMT root matches.

### Impact Explanation
Replay-verify is one of the primary integrity tools operators and the Aptos Labs security/verification pipeline use to detect state divergence, e.g., after a VM/consensus upgrade, hard fork, or bug in execution logic. If the checkpoint hash comparison is silently skipped, a real divergence in the committed state root (Jellyfish Merkle root, hot-state root, or the new "position" state root) will not surface as a replay-verify failure. This directly undermines the "state-commitment integrity" guarantee: a wrong accumulator/state root can be treated as validated, masking bugs that corrupt durable ledger state, and delaying detection of hard-fork-causing bugs until much later (or never, if this is the sole verification gate used in CI/tooling).

### Likelihood Explanation
The gap is deterministic and always present — it is not a race condition or attacker-triggered path; it requires no external input beyond a real divergence bug already existing in local execution/state-computation (e.g., in the new native-position code path this repo is actively developing, as flagged by the TODO's own reference to `COMPUTE_TRADING_NATIVE_STATE_ROOTS`). The comment itself indicates the aptos-core maintainers are aware the feature is currently being staged and unfinished, and that the checkpoint-hash validation was deliberately deferred rather than closed, i.e., the tool is currently shipped without full state-root verification while flagged as intended to gain this check "before enabling" the new feature flag. This makes it a real, currently-present verification gap in a state-integrity tool, but its practical impact is bounded to detection/verification tooling rather than the executor's actual commit path (I did not find evidence that the executor's real commit path, `do_state_checkpoint.rs` / `do_ledger_update.rs`, itself relies on `ensure_match_transaction_info`; those appear to compute state roots directly rather than skip the comparison — this could not be fully confirmed with the remaining budget).

### Recommendation
Extend `ensure_match_transaction_info` to compare locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when locally computable, e.g., not `None`) against the corresponding fields in `txn_info`, and fail replay-verify on mismatch, consistent with the existing `TODO(trading-native)` note.

### Proof of Concept
Not directly exploitable by an external attacker; the gap is a missing self-check. Demonstration path: run `db-tool replay-on-archive` against a backup/version range where local re-execution intentionally produces a divergent JMT/position root (e.g., inject a bug in state-checkpoint computation without changing write-set contents/events/gas/status) — the tool will report the range as verified successfully despite the state root mismatch, because `ensure_match_transaction_info` at [4](#0-3)  never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.

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
