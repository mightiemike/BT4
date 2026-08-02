### Title
Replay/restore verification silently skips state-checkpoint hash comparison, allowing wrong committed state to pass as verified - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant that binds a locally re-executed `TransactionOutput` to the validator-signed `TransactionInfo` during chunk-executor replay (state-sync output-replay, backup restore replay, and `db-tool replay-on-archive` auditing). It checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the post-transaction state (Sparse-Merkle/JMT) root. The code even documents this gap with a `TODO(trading-native)` comment acknowledging it.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` is the sole function used to confirm that a locally recomputed `TransactionOutput` matches the trusted `TransactionInfo` (whose hash is itself bound to a validator-signed `LedgerInfo` via an accumulator proof) before the replayed output is treated as verified and committed. It validates:
- transaction status vs `txn_info.status()`
- `gas_used`
- `write_set_hash == txn_info.state_change_hash()`
- `event_root_hash == txn_info.event_root_hash()`

It never compares the locally computed state/hot-state checkpoint root(s) against `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` — even though `TransactionInfoV1` carries these fields specifically to authenticate world-state roots [2](#0-1) .

This function is invoked in two integrity-critical replay/restore paths:
1. `ChunkExecutorInner::verify_execution`, used when replaying transactions from a backup (`TransactionRestoreController`) or during output-based chunk verification, which decides whether replayed output can be committed via `remove_and_apply` [3](#0-2) .
2. `db-tool`'s `replay_on_archive::execute_and_verify`, the tool explicitly designed to independently audit that historical execution matches the archived/expected results [4](#0-3) .

Because the state-checkpoint root comparison is skipped, if local re-execution produces a *different* Sparse-Merkle/JMT root than the one originally computed and signed by validators (e.g. due to a state-computation bug, feature-flag/versioning mismatch between the executing binary and the version that originally produced the data, or unnoticed nondeterminism in state materialization), the mismatch is not detected. `ensure_match_transaction_info` returns `Ok(())`, and the replay/restore pipeline proceeds to commit the divergent local state as if it had been fully verified.

### Impact Explanation
This breaks the "committed state must match the correct VM result" and "replay/restore paths must preserve deterministic proof binding" invariants required by the State-Integrity Gate. A node performing backup restore or replay-verify can silently persist a state tree whose root does not match the validator-attested root, without any error, warning, or `assert` failure. Because the state root is precisely the authenticated value that downstream state proofs, resource reads, and `state_checkpoint_hash` fields in the API rely on, an operator or auditor using `replay_on_archive` to confirm chain integrity would get a false "success" even when the true (canonical) state diverges — masking exactly the class of hard-fork/consensus divergence bug this tool exists to catch. This is a state-commitment integrity defect with silent corruption and no detection mechanism.

### Likelihood Explanation
This is not exploitable by an external unprivileged attacker triggering a single request — it requires an actual state-computation divergence to occur (e.g., a latent VM/state bug, a version skew between execution logic and previously-committed data, or the upcoming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` trading-native hot-state feature going live while this check remains disabled, as the TODO warns). However, the code path is unprivileged from the caller's perspective — every node running backup restore or `replay_on_archive` executes it unconditionally, and the gap is deterministic and always present regardless of any explicit attacker action.

### Recommendation
Extend `ensure_match_transaction_info` to compare the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the values recomputed from the post-replay state, and fail replay/restore/verification on mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any code path relying on these hashes) is enabled.

### Proof of Concept
Not applicable as a single runnable exploit — the defect is a logic omission (missing checks) provable purely by static reading of `ensure_match_transaction_info`'s body (lines 2139–2204) which never references `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, contrasted with `TransactionInfoV1`'s fields it should be checking against, and the explicit TODO comment in the same function acknowledging the gap.

**Uncertainty**: I could not fully verify from the index whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any other consumer relying on `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is already active on mainnet — the feature flag enum listing was truncated in my search results. If this feature is not yet enabled anywhere and `state_checkpoint_hash` (the V0/legacy field) is otherwise redundantly checked elsewhere in the commit path (e.g., inside `StateStore`/`aptosdb_writer` when materializing the SMT), the practical severity would be lower, since only a general state-computation bug in the base state_checkpoint_hash would need to also bypass any other existing check to be impactful. I was not able to trace whether such a redundant check exists elsewhere in the codebase given the remaining scope of this session.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-707)
```rust
        // not `zip_eq`, deliberately
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
