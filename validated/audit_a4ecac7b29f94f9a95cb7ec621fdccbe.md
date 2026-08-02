Confirmed root cause: `TransactionInfo::ensure_match_transaction_info` in `types/src/transaction/mod.rs` is the shared comparator used by both `execution/executor/src/chunk_executor/mod.rs` (real chunk-executor replay/apply path) and `storage/db-tool/src/replay_on_archive.rs` (`replay_on_archive.rs:392`) to decide whether a freshly re-executed `TransactionOutput` matches the trusted, previously-committed `TransactionInfo`. This single check is applied uniformly to all `TransactionInfo` fields, but those fields are not equally "live": `state_change_hash` and `event_root_hash` are checked every transaction (high update frequency, always populated), while `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are checked only opportunistically/not at all — the function's own trailing comment states the comparator "ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

### Title
`ensure_match_transaction_info` silently skips state/hot-state/position checkpoint hash verification during replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionInfo::ensure_match_transaction_info` [1](#0-0)  validates transaction status, gas used, write-set hash (`state_change_hash`), and event root hash between a freshly computed `TransactionOutput` and an already-committed, trusted `TransactionInfo`, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` [2](#0-1) . This is the exact "outdated"-style analog of the Chainlink bug: one comparator is reused to authenticate multiple state artifacts that have different "update cadence" (per-txn write set/events vs. per-checkpoint/per-epoch state roots), and the comparator's coverage is tuned for the frequent fields while silently under-checking the infrequent ones.

### Finding Description
The function is used verbatim by:
- `storage/db-tool/src/replay_on_archive.rs:392`, the tool operators run to verify that re-executing archived transactions reproduces the exact committed ledger state [3](#0-2) .
- `execution/executor/src/chunk_executor/mod.rs`, which imports and relies on the same output/txn-info matching machinery during chunk replay/execution to accept or reject execution outputs before they are treated as verified.

Because `ensure_match_transaction_info` never checks `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` against the corresponding values computed from local re-execution, a divergence in state-checkpoint computation (whether from a local VM/state-tree bug, a different `TransactionInfo` builder version, a bad `position_state_checkpoint_hash` under the new "trading native state roots" feature, or corrupted archived data feeding the "expected" `TransactionInfo`) will not be flagged: the comparator returns `Ok(())` even though the authenticated state root recorded in the ledger and the actual locally-computed state root disagree.

### Impact Explanation
This breaks the state-integrity invariant that "committed state that differs from the correct VM result" must be detectable. Replay-verify — the primary tool operators and auditors use to confirm that a node/archive's on-chain root hashes are the true product of deterministic re-execution — can report success while state-checkpoint/hot-state/position-state roots have silently diverged. If the archived `TransactionInfo` itself is wrong (bit-flip, corrupted backup, malicious archive source, or a code regression that alters checkpoint-hash computation without changing the write set/events), the check does not catch it, and replay verification gives false assurance of correctness for exactly the fields that anchor state Merkle proofs used by light clients and the API.

### Likelihood Explanation
The gap is unconditionally present in the code today (not gated behind a disabled feature flag check inside the function itself); the function's own doc comment acknowledges it explicitly and calls out `COMPUTE_TRADING_NATIVE_STATE_ROOTS` as a feature that would make this gap consequential once state-checkpoint/position roots are actively produced and relied upon. Today, ordinary transactions mostly don't carry a `state_checkpoint_hash` except at checkpoint boundaries, so the immediate blast radius is limited to checkpoint/epoch-boundary transactions and to the newer position/hot-state root fields, but the check is exercised on every replay run and is trivially reachable by any operator or automated test running `replay_on_archive` or by the chunk executor during normal state-sync/backup restore replay.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` between the locally computed output and `txn_info` whenever those hashes are expected to be present, mirroring the treatment already given to `state_change_hash` and `event_root_hash`. Until then, callers such as `replay_on_archive.rs` and the chunk executor should not be treated as authoritative for checkpoint/state-root integrity.

### Proof of Concept
1. Take any committed `TransactionInfo` at a state-checkpoint boundary, and its persisted `state_checkpoint_hash`/`position_state_checkpoint_hash`.
2. Corrupt only the checkpoint hash byte(s) in the archived/backup `TransactionInfo` fed as `expected_txn_info`, leaving `transaction_hash`, `event_root_hash`, `state_change_hash`, `gas_used`, and `status` intact.
3. Run `db-tool replay-on-archive` (or trigger the chunk executor's replay path) over this transaction: `ensure_match_transaction_info` [1](#0-0)  returns `Ok(())` because it only checks status, gas, write-set hash, and event root hash — the corrupted checkpoint hash is never inspected, so the tool reports a passing replay despite the state root discrepancy. [4](#0-3) [3](#0-2)

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
