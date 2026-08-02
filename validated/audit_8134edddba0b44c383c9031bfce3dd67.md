## Analysis

The external report's core pattern — a value-combining/migration operation that skips a bound check that exists elsewhere in the same protocol (`setValidatorAddress` merging stakes without re-checking `validatorMaxStake`) — maps to Aptos-native code where a *verification* routine that is supposed to bind committed data to an authenticated value silently skips part of that binding.

I traced several proof/commit-integrity paths (write-set conversion, accumulator append, JMT/state-restore chunk verification, state-sync snapshot receivers, hot-state / native-position checkpoint commit). Most of these correctly verify every relevant hash before accepting data (e.g. `state-sync-driver/src/bootstrapper.rs` verifies chunk root hash before creating a receiver, `storage/aptosdb/src/state_restore/mod.rs::add_chunk` always verifies the JMT proof before writing). The one place where a hash-binding check is explicitly and intentionally dropped — with a code comment acknowledging the gap — is `TransactionOutput::ensure_match_transaction_info`.

### Title
Replay-verify accepts an execution whose checkpoint state roots diverge from the authenticated `TransactionInfo` - (`types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info`, the function used by replay/verification tooling to bind a freshly re-executed `TransactionOutput` to an already-committed, signature-authenticated `TransactionInfo`, checks status, gas, write-set hash (`state_change_hash`) and event root hash, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` is the sole gate used to confirm that a locally re-executed transaction reproduces the ledger's authenticated result: [2](#0-1) 

The function's trailing comment documents the gap directly:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is invoked directly by `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, which drives the archive replay-verification tool: it re-executes a batch of historical transactions and calls `ensure_match_transaction_info` against `expected_txn_infos` pulled from the archive/backup to decide whether replay succeeded: [4](#0-3) 

Because `state_checkpoint_hash` (the Sparse-Merkle-Tree root of world state at a checkpoint), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (the native-position state root, gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) are excluded from the comparison, any divergence between the locally computed state root and the one baked into the archived, signed `TransactionInfo` is invisible to this tool. The same `TransactionInfoV1` structure carries these roots into the transaction accumulator and downstream state-sync bootstrapping logic (`bootstrapper.rs::expected_snapshot_root`), which trusts `position_state_checkpoint_hash`/`state_checkpoint_hash` as the ground truth for fast-sync snapshot verification: [5](#0-4) 

### Impact Explanation
Replay-verification is the mechanism operators and the Aptos Labs team use to detect state divergence bugs (VM bugs, executor bugs, or storage corruption) between the canonical/archived chain history and a freshly re-executed replay. Because the comparator omits `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, a bug that corrupts the Sparse-Merkle-Tree root computation (main state, hot state, or the newly introduced native-position state) — silently committing a wrong world-state root at a checkpoint — will not be flagged by `replay_on_archive`. This directly matches the Gate's "committed state that differs from the correct VM result... accepted as valid" and "authenticated API or state-view output bound to the wrong version, object, or proof context" criteria: the authenticated `TransactionInfo.state_checkpoint_hash` is the field that binds a version to its state root, and the tool meant to authenticate it against re-execution ignores it.

### Likelihood Explanation
The gap is unconditionally present today for `state_checkpoint_hash` and `hot_state_checkpoint_hash` in all replay-verify runs (not just when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) since `ensure_match_transaction_info` has no feature gate around the missing checks — it simply never checks these fields, comment notwithstanding. The comment only calls out the risk for the not-yet-enabled native-position feature, but the code as written already silently skips `state_checkpoint_hash`/`hot_state_checkpoint_hash` for every replay. This is triggerable any time replay-verify tooling is used to validate a segment of history where the state root diverges (e.g. after a state-computation bug), and requires no attacker privilege — it's a self-inflicted verification blind spot.

### Recommendation
Add explicit comparisons in `ensure_match_transaction_info` for `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/expected) against the recomputed roots before treating a replayed chunk as validated, and gate `COMPUTE_TRADING_NATIVE_STATE_ROOTS` rollout on this fix as the existing TODO already recommends.

### Proof of Concept
1. Take any historical version range where an execution bug causes the locally computed state-checkpoint SMT root (or hot-state / position-state root) to differ from the value stored in the archived `TransactionInfo`.
2. Run `aptos-db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`) over that range.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which checks `status`, `gas_used`, `write_set_hash`, and `event_root_hash` only — none of which detect a state-root divergence.
4. The tool reports the replay as successful even though the state root, which is a proof-bearing, authenticated field baked into every `LedgerInfoWithSignatures`, is wrong.

**Uncertainty**: I could not fully verify whether any other independent path in the consensus/execution pipeline (outside of `replay_on_archive`) re-validates `state_checkpoint_hash` against local re-execution during normal chunk-executor commit (as opposed to backup/replay verification) within the tool's search budget; `execution/executor/src/chunk_executor/mod.rs` also references `ensure_match_transaction_info` but I was not able to inspect that call site's surrounding logic in this session.

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

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L996-1008)
```rust
        match kind {
            StateKind::MainState => target_transaction_info
                .ensure_state_checkpoint_hash()
                .map_err(|error| {
                    Error::UnexpectedError(format!(
                        "State checkpoint must exist! Error: {:?}",
                        error
                    ))
                }),
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
```
