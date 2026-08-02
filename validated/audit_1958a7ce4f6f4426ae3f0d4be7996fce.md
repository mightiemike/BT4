Based on my investigation, the strongest local candidate is a documented, unpatched gap in `TransactionOutput::ensure_match_transaction_info`.

### Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/verify tooling to accept a corrupted state/position root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the authenticated-response/replay analog of the "unvalidated external call" pattern: it consumes a `TransactionInfo` (an authenticated, proof-bearing structure) and is supposed to fully validate a locally-computed `TransactionOutput` against it before the caller trusts the result. It validates status, gas, `state_change_hash` (write set), and `event_root_hash`, but it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is explicitly called out in a TODO comment in the function itself: [1](#0-0) .

### Finding Description
`ensure_match_transaction_info` is used by replay/verify tooling to confirm that locally re-executed transactions produce the exact same committed state as the authenticated `TransactionInfo` stream (which is itself proven against a ledger-info-signed accumulator root). Two call sites rely on it to gate acceptance of a replay as "matching":
- `execution/executor/src/chunk_executor/mod.rs` `verify_execution` (used by `db-tool`/replay-verify flows with `VerifyExecutionMode`) [2](#0-1) .
- `storage/db-tool/src/replay_on_archive.rs` `execute_and_verify` [3](#0-2) .

The check omits `state_checkpoint_hash` (the JMT/state root recorded per-transaction), `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (the "trading native" position-tree root gated by `compute_trading_native_state_roots`) [4](#0-3) . The function's own comment confirms: *"this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."* This is a self-admitted broken invariant in verification logic, not a speculative one.

By contrast, the online chunk-executor commit path (`update_ledger` in the same file) does separately recompute and compare `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` via `DoStateCheckpoint::run(...).maybe_known_state_checkpoints(...)` before persisting [5](#0-4) , so live state-sync commit is protected. The gap is isolated to the `ensure_match_transaction_info`-based replay/verify comparator.

### Impact Explanation
This affects operator/tooling-facing replay verification (e.g., `aptos db-tool replay-on-archive`, `VerifyExecutionMode::verify_all`), which is the mechanism used to detect state divergence such as hard-fork bugs or execution nondeterminism when the `compute_trading_native_state_roots` feature path is enabled. Because the checkpoint hashes aren't compared, a divergence limited to the state/hot-state/position checkpoint root (while write set, events, gas, and status still match) would go undetected by this specific comparator, causing replay-verify to falsely report success on a node whose committed state root actually diverges from the authenticated ledger. Since the feature is explicitly named ("trading native state roots") and gated by on-chain config, this is a mainnet-relevant proof-integrity gap once that feature is enabled, matching the "authenticated API/state-view output bound to wrong root" impact category in the gate.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires (a) the `compute_trading_native_state_roots` / hot-state-in-txn-info features to be active, and (b) an actual divergence limited to the checkpoint-hash fields (e.g., a bug in JMT/position-tree computation or hot-state promotion) that leaves write-set/event/gas/status untouched. The bug itself does not directly corrupt state — it weakens a verification tool's ability to detect corruption, which is why the code's own author flagged it as a known, accepted gap rather than an active exploit.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally-computed values whenever those fields are populated in the given `TransactionInfo` version, as the code comment itself already recommends, before enabling/relying on `compute_trading_native_state_roots` in replay-verify tooling.

### Proof of Concept
Not independently exploitable as a live consensus/state-commit bug — the gap is confined to the offline verification comparator. Trace: `chunk_executor::verify_execution` / `replay_on_archive::execute_and_verify` call `TransactionOutput::ensure_match_transaction_info` [2](#0-1)  → function checks status/gas/write_set_hash/event_root_hash only [6](#0-5)  → checkpoint-hash fields of `txn_info` (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are read elsewhere (e.g. `assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs:82-121`) but never compared here → a re-executed output with a diverging checkpoint root but identical write set/events/gas/status passes `ensure_match_transaction_info` without error, so replay-verify reports success despite a real state-root divergence.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
