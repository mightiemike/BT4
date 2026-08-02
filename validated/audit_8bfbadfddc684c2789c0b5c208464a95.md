### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify accept a divergent position/state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` — the function used by replay/verify tooling (`db-tool replay-on-archive`, `aptos-debugger`, `aptos-move/cli`) to prove that a locally re-executed `TransactionOutput` matches the authenticated, committed `TransactionInfo` from an archive/backup — deliberately omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. This is acknowledged in the code itself as a known gap.

### Finding Description
`ensure_match_transaction_info` verifies status, gas, write-set hash, and event-root hash against the `TransactionInfo`, but explicitly does **not** check the checkpoint hashes: [1](#0-0) 

The comment inline states the exact integrity gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This is precisely the invariant class targeted by the task: authenticated proof/state data (the `state_checkpoint_hash`/`position_state_checkpoint_hash` fields inside `TransactionInfo`, which are themselves committed into the transaction accumulator and thus into the ledger's authenticated proof chain) must be checked against the freshly-computed values during replay/verification. `ensure_match_transaction_info` is called directly in `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` as the sole per-transaction correctness gate during replay against an already-committed, signed `TransactionInfo`/ledger info. If the write-set/event hashes match but the state (or position) Merkle root computed locally diverges from the one embedded in the authenticated `TransactionInfo`, this function still returns `Ok(())`, i.e. replay-verify falsely reports success.

Elsewhere in the codebase, the checkpoint hash comparison is treated as security-critical when it *is* performed — e.g. `DoStateCheckpoint::get_state_checkpoint_hashes` hard-fails (`ensure!`) on a checkpoint hash mismatch against known hashes during normal chunk execution/commit: [3](#0-2) 
and `chunk_executor::update_ledger` collects `known_position_state_checkpoints`/`known_state_checkpoints` from `TransactionInfo`s specifically to re-validate the computed roots against the committed ones: [4](#0-3) 
This confirms the checkpoint hash is treated as an integrity-critical, must-match field in the "hot" commit path — but the parallel comparator used by replay/verify tooling (`ensure_match_transaction_info`) intentionally skips this same check.

### Impact Explanation
Replay-verify tooling is one of the primary mechanisms operators and auditors use to independently confirm that a historical, signed ledger segment corresponds to the correct VM execution result (i.e., to detect state-commitment corruption, storage bugs, or non-determinism after the fact). Because `ensure_match_transaction_info` does not compare `state_checkpoint_hash`/`position_state_checkpoint_hash`, a divergence in the committed state root (e.g. from a storage bug, an execution non-determinism bug, or a corrupted/backup-manipulated `TransactionInfo` state field) will not be caught by this specific validation path even though the write set and events match — the tool will report "replay succeeded" while the state root actually diverges. This directly matches the "state-integrity gate" impact class: "committed state that differs from the correct VM result... accepted as valid" via an authenticated verification path. It is currently gated behind the not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature for the position-state portion, but the `state_checkpoint_hash`/`hot_state_checkpoint_hash` gap applies unconditionally to all replay/verify invocations today, independent of that feature.

### Likelihood Explanation
Likelihood is limited by the fact that write-set and event-hash checks still catch most classes of execution divergence (most bugs that corrupt state also corrupt the write set going into the same transaction). The gap specifically matters for cases where the *reduction of the write set into the persisted Merkle tree/checkpoint root* is wrong even though the write set itself is right, or where a non-determinism affects checkpoint construction rather than the write set (e.g., a bug in `assemble_transaction_infos`/`DoStateCheckpoint` root computation, or a manipulated archive/backup `TransactionInfo`). Given the code's own TODO calls this out as unfinished work tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, the maintainers are aware but it is currently un-remediated for the general (non-position) checkpoint-hash case as well.

### Recommendation
Extend `ensure_match_transaction_info` to also assert that a locally-recomputed `state_checkpoint_hash` (and, once enabled, `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) matches `txn_info.state_checkpoint_hash()` whenever the checkpoint is expected to have been computed for that transaction (mirroring the `ensure!` pattern already used in `DoStateCheckpoint::get_state_checkpoint_hashes`), before allowing `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any other reliance on this comparator for correctness) to be enabled.

### Proof of Concept
Not applicable as a runnable exploit — the finding is a code-level completeness gap in an auditing/verification routine, demonstrated directly by the acknowledged TODO and the absence of checkpoint-hash assertions in `ensure_match_transaction_info` compared to the equivalent, correctly-guarded check in `DoStateCheckpoint::get_state_checkpoint_hashes`. The uncertain part I could not fully verify given the read-only/limited exploration is whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is already enabled on mainnet (the feature-flag default state in `aptos_features.rs` was not fully confirmed), which affects how severe the position-state portion of this gap currently is; the `state_checkpoint_hash` omission, however, is unconditional and applies today regardless of that flag.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L206-220)
```rust
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L373-413)
```rust
        let txn_infos = chunk_verifier.transaction_infos();
        let known_state_checkpoints = Some(
            txn_infos
                .iter()
                .map(|t| t.state_checkpoint_hash())
                .collect_vec(),
        );
        let known_hot_state_checkpoints =
            output.execution_output.hot_state_root_in_txn_info.then(|| {
                txn_infos
                    .iter()
                    .map(|t| t.hot_state_checkpoint_hash())
                    .collect_vec()
            });
        let compute_trading_native_state_roots =
            output.execution_output.compute_trading_native_state_roots;
        let known_position_state_checkpoints = compute_trading_native_state_roots.then(|| {
            txn_infos
                .iter()
                .map(|t| t.position_state_checkpoint_hash())
                .collect_vec()
        });
        let position_persisted = compute_trading_native_state_roots
            .then(|| ProvablePositionStateSummary::new_persisted(self.db.reader.as_ref()))
            .transpose()?;
        let state_checkpoint_output = DoStateCheckpoint::run()
            .execution_output(&output.execution_output)
            .parent_state_summary(&parent_state_summary)
            .persisted_state_summary(&ProvableStateSummary::new_persisted(
                self.db.reader.as_ref(),
            )?)
            .maybe_known_state_checkpoints(known_state_checkpoints)
            .maybe_known_hot_state_checkpoints(known_hot_state_checkpoints)
            // Parent position summary is chained across chunks by the commit
            // queue (seeded from the pre-committed position tip); the persisted
            // base supplies cold-key proofs. The known-hash check validates the
            // computed root against the committed TransactionInfos.
            .maybe_parent_position_state_summary(parent_position_state_summary.as_ref())
            .maybe_persisted_position_state_summary(position_persisted.as_ref())
            .maybe_known_position_state_checkpoints(known_position_state_checkpoints)
            .build()?;
```
