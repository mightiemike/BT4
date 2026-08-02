### Title
Replay-Verification Comparator Ignores State/Hot-State/Position Checkpoint Hashes, Masking Committed State-Root Divergence - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` — the function used by replay-verification tooling to confirm a freshly re-executed transaction matches the authenticated `TransactionInfo` recorded on-chain — checks transaction status, gas used, write-set hash, and event root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is called out explicitly in a TODO comment in the code itself, confirming it is a known, unaddressed gap rather than a speculative finding.

### Finding Description
`ensure_match_transaction_info` [1](#0-0)  validates only:
- `status`
- `gas_used`
- write-set hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

It never touches `txn_info.state_checkpoint_hash()`, the hot-state checkpoint hash, or `position_state_checkpoint_hash()`. The code's own comment documents this: [2](#0-1) 

This function is the sole verification predicate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions with the VM and calls `ensure_match_transaction_info` to decide pass/fail: [3](#0-2) . Unlike the live commit path — where `DoStateCheckpoint::run` explicitly recomputes the state/hot-state/position checkpoint roots and validates them against `known_state_checkpoints` via `get_state_checkpoint_hashes` [4](#0-3)  — the offline replay-verify/archive-replay tool bypasses `DoStateCheckpoint` and only relies on `ensure_match_transaction_info`. As a result, if a locally re-executed transaction produces a different state root (main state, hot state, or the trading-native `position_state_checkpoint_hash`) than what is embedded in the authenticated `TransactionInfo` fetched from the backup/archive, the tool reports success anyway.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... accepted as valid" and "authenticated ... output bound to the wrong ... proof context" integrity properties for the archive replay/verification tooling: a divergent state root (e.g., from a bug in state-checkpoint computation, a non-determinism in JMT/position-state hashing, or a malicious/corrupted archive) would not be caught by `replay_on_archive`/`replay_verify`, even though these tools exist specifically to detect exactly this class of divergence. This is particularly acute for `position_state_checkpoint_hash`, which is the authenticated root for the newer "trading-native" state tree feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`), meaning the verification blind spot is directly on a proof-bearing, freshly-introduced state root.

### Likelihood Explanation
The gap is deterministic and always present — every call to `ensure_match_transaction_info` skips these fields, not just under rare conditions. However, actual exploitation (or triggering) requires an underlying state-root computation divergence to exist elsewhere (e.g., a bug in `DoStateCheckpoint`/JMT/position-state logic) that this comparator would otherwise have caught; on its own this function does not corrupt committed state, it only fails to detect a corruption that already occurred. This limits it to a detection/verification-integrity issue rather than a direct on-chain state-corruption primitive, and the comment indicates the maintainers are already aware and plan to gate on it before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

### Recommendation
Extend `ensure_match_transaction_info` to compare `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (for V1 infos), and `position_state_checkpoint_hash()` against locally recomputed values whenever those hashes are expected to be present for the given transaction (i.e., when it is a checkpoint boundary and/or `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled), and fail replay verification on any mismatch, matching the validation already performed by `DoStateCheckpoint::get_state_checkpoint_hashes` in the live commit path.

### Proof of Concept
Not independently exploitable as a standalone PoC without also inducing a real state-checkpoint-hash divergence elsewhere in the pipeline (e.g., via the trading-native/position-state extension logic in `do_state_checkpoint.rs`). Conceptually:
1. Introduce (or naturally hit, via a pre-existing determinism bug) a scenario where locally-recomputed `position_state_checkpoint_hash` (or `state_checkpoint_hash`) differs from the one stored in the archived `TransactionInfo`.
2. Run `storage/db-tool/src/replay_on_archive.rs` against that archive.
3. Observe that `execute_and_verify` calls `ensure_match_transaction_info`, which passes because it never compares the checkpoint hash fields, so the tool reports a successful replay despite the state-root divergence [3](#0-2) .

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-397)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-60)
```rust
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
```
