### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, letting replay/verify tooling accept a divergent state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` (used by `ChunkExecutor::verify_execution` for `TransactionReplayer`, `aptos-debugger`, `db-tool replay_on_archive`, and CLI simulation-verification commands) checks status, gas, write-set hash, and event root hash against a trusted `TransactionInfo`, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is the same asymmetric-check pattern as the Peapods `closeFee` bug: one component of the committed result (write set / events) is checked, while a sibling component that is part of the same authenticated commitment (the state root) is silently exempted.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates a freshly produced `TransactionOutput` against a previously persisted/authenticated `TransactionInfo`. It checks:
- execution status
- gas used
- write-set hash vs. `state_change_hash`
- event root hash vs. `event_root_hash`

It does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that represent the actual state-tree/JMT/position-tree roots committed to the ledger accumulator. The code itself documents this gap: [2](#0-1) 

This comparator is the sole “does replay match what was authenticated” check used by `ChunkExecutorInner::verify_execution`, which re-executes transactions locally and asserts the freshly computed `TransactionOutput` matches the trusted (proof-backed) `write_sets`/`transaction_infos` supplied to `TransactionReplayer::enqueue_chunks`: [3](#0-2) 

Because this is the only equality gate applied here, a local execution that produces the correct write set and events but a different resulting state root (e.g., due to a state-checkpoint/hot-state/position-state computation bug, feature-flag mismatch such as `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO`, or non-determinism in checkpoint-hash derivation) will still return `Ok(())` and be treated as a verified match.

### Impact Explanation
This directly falls into the “Hard-fork-only divergence during commit, replay, restore, or proof verification” impact category: replay-verify tooling (`db-tool replay_on_archive`, `aptos-debugger`, CLI transaction-verification commands) is the mechanism operators rely on to detect execution non-determinism before/during hard forks and to validate archived/backed-up history against the authenticated chain. If this comparator silently accepts a state-root divergence, an execution bug that corrupts the state checkpoint hash (main state, hot state, or the native-trading position tree) can pass replay verification undetected, masking a consensus-breaking bug and allowing corrupted historical state to be treated as validated. The comment explicitly calls out that this must be fixed “before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`,” confirming the authors recognize this as a real, unresolved verification gap tied to a state-root commitment.

### Likelihood Explanation
The gap requires no attacker action to trigger — it is a latent blind spot in an already-shipped verification function used by every consumer of `ensure_match_transaction_info` (4 call sites: `aptos-debugger`, CLI, `db-tool/replay_on_archive.rs`, `chunk_executor`). It surfaces whenever any code path produces a state checkpoint/position-state root that differs from the trusted `TransactionInfo` — which is precisely the class of bug replay-verify tooling exists to catch. Likelihood of the underlying state-root divergence occurring is separate from this issue, but likelihood that *this specific check* fails to catch it is 100% given the current code.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present, e.g. gated by `TRANSACTION_INFO_V1`/`HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) between the locally computed output and the trusted `txn_info`, mirroring the write-set/event checks already present, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` as the code comment itself requires.

### Proof of Concept
Not applicable as a runnable exploit (this is a verification-tooling gap, not a state-transition bug by itself). Conceptual trigger: construct/replay a chunk where the freshly-executed `TransactionOutput`'s write set and events match the persisted `TransactionInfo` but the locally computed state checkpoint hash (or, with `COMPUTE_TRADING_NATIVE_STATE_ROOTS` enabled, the position-state checkpoint hash) differs from `txn_info.state_checkpoint_hash()` / `txn_info.position_state_checkpoint_hash()`. Calling `ensure_match_transaction_info` on this pair returns `Ok(())`, so `ChunkExecutorInner::verify_execution` ( [4](#0-3) ) and downstream tools (`db-tool replay_on_archive`, `aptos-debugger`) report a clean replay despite the state root having diverged.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-708)
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
    }
```
