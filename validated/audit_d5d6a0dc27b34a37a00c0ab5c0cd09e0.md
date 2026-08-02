This confirms a concrete, self-documented integrity gap in `TransactionOutput::ensure_match_transaction_info`, used by `chunk_executor::verify_execution` for replay-verification against persisted/authenticated ledger data.

### Title
Replay-verification skips validating state/hot-state/position checkpoint hashes, allowing divergent committed state to pass as verified - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function chunk-executor's replay verification (`verify_execution`) and related tooling use to confirm that a locally re-executed `TransactionOutput` matches the authenticated, on-chain `TransactionInfo` for a given version. It validates status, gas, write-set hash, and event-root hash, but explicitly and knowingly omits validation of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function compares `self.status()`, `gas_used()`, the write-set hash against `txn_info.state_change_hash()`, and the event-root hash against `txn_info.event_root_hash()`, then returns `Ok(())` without ever consulting `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. The comment directly preceding the `Ok(())` acknowledges the gap: [2](#0-1) 

This function is invoked by `ChunkExecutorInner::verify_execution`, which is the core correctness check used when replaying a chunk of persisted/backed-up transactions against locally re-executed output: [3](#0-2) 

This is the same mechanism used by `storage/db-tool`'s `replay_verify`/`replay_on_archive` tooling, which is the tool operators and the community use to authenticate that an archived/backed-up ledger truthfully reflects VM execution results, i.e., the mechanism referenced directly in the code comment ("replay-verify tooling (e.g. db-tool's `replay_on_archive`)").

Because `TransactionInfoV1.state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are part of the committed, accumulator-hashed `TransactionInfo` (and thus part of the authenticated ledger and covered by consensus signatures via the ledger accumulator), any bug in state-checkpoint-hash computation — in `DoStateCheckpoint`, hot-state root computation (`update_hot_state_summary`), or native-position root computation (`compute_position_checkpoint`) — would go completely undetected by replay-verification. The comment states this omission is deliberate scaffolding to be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`", i.e., the authors know today's state is insecure for that feature but the gate is a to-do, not an enforced code invariant.

### Impact Explanation
Replay-verify / archive-replay tooling is a primary integrity backstop used to detect if an archived Aptos ledger backup's transaction outputs, and therefore the resulting state, actually match honest VM execution. Silently skipping verification of `state_checkpoint_hash` (and the newer hot-state/position-state checkpoint hashes) means:
- A corrupted, tampered, or buggy state root committed into `TransactionInfo` (and thus baked into the transaction accumulator and ultimately signed by validators) would not be flagged by `replay_on_archive`/`replay_verify`, even though these tools are specifically relied upon to catch exactly this class of divergence.
- Since `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are not independently recomputed and compared during replay-verify, any hard-fork-only divergence in state-checkpoint computation (e.g., from the newly introduced hot-state or native-position code paths) is silently accepted as "verified", defeating the entire purpose of the check for those fields.

This matches the required impact category "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Committed state that differs from the correct VM result... accepted as valid" directly.

### Likelihood Explanation
This is not a hypothetical attack chain requiring a malicious peer — it is a proven, currently-existing gap in the code's own correctness-checking logic, applicable any time replay-verify tooling is used, and the code authors themselves flag it as unsafe once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or, more relevantly, any state/hot-state checkpoint divergence) occurs. Given hot-state and native-position checkpoint hashing are new, actively-developed features, the likelihood of a computation bug slipping past replay-verify specifically because of this gap is elevated.

### Recommendation
Extend `ensure_match_transaction_info` to recompute or otherwise validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against the state produced by re-execution, mirroring how `state_change_hash` and `event_root_hash` are already validated. At minimum, callers such as `verify_execution` in `execution/executor/src/chunk_executor/mod.rs` should independently assert equality of these checkpoint hashes before declaring successful replay, rather than relying solely on this helper.

### Proof of Concept
1. Run `replay_verify` / `replay_on_archive` (`storage/db-tool/src/replay_verify.rs`, `storage/db-tool/src/replay_on_archive.rs`) against an archive containing a chunk whose persisted `TransactionInfo.state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) does not match what local re-execution would produce (e.g., simulate by constructing a `TransactionInfo` with a mutated checkpoint hash but correct `state_change_hash`/`event_root_hash`/`gas_used`/`status`).
2. Call `ChunkExecutorInner::verify_execution` (`execution/executor/src/chunk_executor/mod.rs:648-708`), which internally calls `TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`).
3. Observe that `ensure_match_transaction_info` returns `Ok(())` despite the checkpoint-hash mismatch, because the function never inspects `txn_info.state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`, causing `verify_execution` to report success for a divergent, incorrect state root.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
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
```
