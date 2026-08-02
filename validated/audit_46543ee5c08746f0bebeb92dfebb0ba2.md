## Analysis Result

I traced the external report's core invariant — "computed result may diverge from what was actually consumed/checked, but the mismatch is silently accepted" — into the Aptos replay-verification path. The closest local analog is in `TransactionOutput::ensure_match_transaction_info`.

### Title
Chunk replay-verification does not validate state/hot-state/position checkpoint hashes, allowing a diverged state root to pass verification - (File: `types/src/transaction/mod.rs`)

### Summary
`ensure_match_transaction_info` is the function used by the chunk executor's `verify_execution` path (and by db-tool's `replay_on_archive`) to confirm that a locally re-executed `TransactionOutput` matches an authenticated `TransactionInfo` pulled from storage/backup. It checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`verify_execution` in the chunk executor re-executes a batch of transactions and calls `ensure_match_transaction_info` per transaction to confirm the recomputed `TransactionOutput` is consistent with the persisted/authenticated `TransactionInfo`: [2](#0-1) 

The comparator itself only validates transaction status, gas used, write-set hash (`state_change_hash`), and event root hash: [3](#0-2) 

It never compares the recomputed state-checkpoint hash (JMT root), hot-state checkpoint hash, or the newer `position_state_checkpoint_hash` field against the values carried in the authenticated `TransactionInfo`. The code contains an explicit acknowledgment of this gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [4](#0-3) 

During normal block execution, checkpoint-hash consistency for the *current* execution run is separately checked in `DoStateCheckpoint::run` / `get_state_checkpoint_hashes`, which compares freshly computed roots against `known_state_checkpoints`/`known_position_state_checkpoints` passed in from the caller: [5](#0-4) 

However, the chunk-replayer's `verify_execution` path (used for restore/replay verification against archived, potentially externally-supplied `TransactionInfo`s) never routes through `DoStateCheckpoint`, and instead relies solely on `ensure_match_transaction_info`, which skips the checkpoint-hash fields entirely. This means the state/hot-state/position checkpoint roots embedded in a `TransactionInfo` — the very roots that authenticated API responses and proofs (state proofs bound to `TransactionInfo.state_checkpoint_hash`) rely on — are never independently re-derived and cross-checked during chunk replay-verification.

### Impact Explanation
If the locally-recomputed state (JMT) root or the newer native "trading" position-state root diverges from the authenticated `TransactionInfo` supplied by an untrusted/archival source (e.g., corrupted backup, buggy trading-native code path, or a discrepancy introduced by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature once enabled), `db-tool replay-on-archive` and the general chunk-replay verification flow will report success even though the actual committed state diverges from the correct VM result. This directly matches the state-integrity gate's concern about "committed state that differs from the correct VM result" and "authenticated API or state-view output bound to the wrong ... proof context" going undetected by the verification tooling meant to catch exactly this class of bug.

### Likelihood Explanation
The gap is exploitable/observable today for any divergence not otherwise caught by write-set/event-hash comparison — e.g., silent state-root corruption limited to the checkpoint/JMT layer, or (as the TODO states) once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on, a divergence in the native position-state root. The code’s own comment flags this as a known, currently-unaddressed prerequisite before enabling that feature, indicating the maintainers are aware but the check is not yet wired in — likelihood is Medium because it requires a concurrent state-computation bug or malicious/corrupted archive input to actually trigger a divergence, but no additional privilege is needed to exploit the gap once such a divergence exists.

### Recommendation
Extend `ensure_match_transaction_info` (or the call site in `verify_execution`) to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the values in the provided `TransactionInfo`, consistent with what `DoStateCheckpoint::get_state_checkpoint_hashes` already does for live execution. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as the existing TODO comment already indicates.

### Proof of Concept
Not independently reproducible without a concrete state-root-diverging execution bug; the finding is that **if** such a divergence occurs (in the state/hot-state/position checkpoint computation), `ensure_match_transaction_info` — and therefore chunk-replay verification and `db-tool replay_on_archive` — will not detect it, because those fields are structurally absent from the comparison in `types/src/transaction/mod.rs:2139-2204`. I was unable to fully verify whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently enabled by default on mainnet (only found flag plumbing in `types/src/on_chain_config/aptos_features.rs`), which affects whether the position-state-root portion of this gap is presently reachable; the state/hot-state checkpoint hash gap in replay verification, however, exists independent of that feature flag.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-707)
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
        Ok(end_version)
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-221)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
```
