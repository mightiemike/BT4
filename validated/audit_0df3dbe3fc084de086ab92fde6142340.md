### Title
`ensure_match_transaction_info` skips `state_checkpoint_hash` verification, letting replay/restore paths accept a divergent committed state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  is the single integrity gate used by every replay/verify tool (`aptos-debugger`, `aptos-move/cli` replay, `db-tool`'s `replay_on_archive`, and the mainline `ChunkExecutorInner::verify_execution` used by `TransactionReplayer` for backup restore and replay-verify) to confirm that a freshly re-executed transaction reproduces the authenticated, on-chain-committed result. It checks `status`, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash, but it deliberately does **not** check `TransactionInfo::state_checkpoint_hash` (or the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`). The code even carries a developer TODO admitting this gap [2](#0-1) .

### Finding Description
This is directly analogous to the Ajna bug pattern: one code path (`DoStateCheckpoint`, used by the normal chunk-executor `update_ledger()` flow for state-sync) validates the computed state root against the "known" (proof-authenticated) `state_checkpoint_hash` from `TransactionInfo` [3](#0-2) , but the "shared" validation helper actually used by replay/verify/restore-oriented paths — `ensure_match_transaction_info` — omits this check entirely. Callers of this helper include:
- `execution/executor/src/chunk_executor/mod.rs::verify_execution` (used by `TransactionReplayer::enqueue_chunks`, the code path that backs backup restoration / `replay-verify`) [4](#0-3) 
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` [5](#0-4) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs::print_mismatches` [6](#0-5) 
- `aptos-move/cli/src/commands.rs` transaction replay [7](#0-6) 

Because `write_set_hash == txn_info.state_change_hash()` only proves the write-set *bytes* match what was recorded, and the state root (`state_checkpoint_hash`) that is actually persisted and used to serve state proofs is never independently cross-checked in this shared helper, any divergence between the recomputed JMT/state root and the historically committed root during replay, restore, or replay-verify will not be detected as a mismatch. The comment explicitly calls this out for the new "trading-native" position state root (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) and hot-state root, meaning as those features are enabled, `replay-verify`/backup-restore tooling will report success even when the authenticated state root diverges from what local execution produces.

### Impact Explanation
`replay-verify` and backup-restore exist specifically to give a cryptographic guarantee that historical state committed to storage/backups is correct and reproducible; that is their entire purpose as an integrity check for durable ledger data (falls under "restore" and "replay" divergence in the State-Integrity Gate). If the state-checkpoint (state root) comparison is silently skipped, a corrupted backup, a JMT/restore bug, or a discrepancy introduced by new state trees (hot-state, native position-state) will pass verification even though the actual committed/restored state diverges from the authenticated one — i.e., authenticated proof-bearing state data (the checkpoint hash embedded in `TransactionInfo`, itself proven against the accumulator/ledger info) is effectively never validated against the state actually being restored. This is a genuine proof/commitment-integrity gap, not merely a rounding/event-only issue.

### Likelihood Explanation
The gap is unconditional in the current code (not just for `COMPUTE_TRADING_NATIVE_STATE_ROOTS`): `ensure_match_transaction_info` never inspects `state_checkpoint_hash` for any transaction, so it triggers whenever `db-tool replay-verify`, `replay_on_archive`, `aptos-debugger`, or `aptos move cli replay` (or state-sync fast-catch-up via `TransactionReplayer`) is used to validate historical execution — these are exactly the tools operators and auditors rely on to detect exactly this class of divergence, so the missing check meaningfully weakens their guarantee. It requires no attacker privilege beyond controlling or corrupting a backup/replay source, or a latent restore/JMT bug going undetected.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the recomputed state-checkpoint hash (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against `txn_info`'s corresponding fields, mirroring the "known state checkpoint" validation already performed by `DoStateCheckpoint` in the normal chunk-executor path, before any feature that produces additional native state roots (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is enabled.

### Proof of Concept
Not independently reproducible from static analysis alone — the gap is a code-level omission (confirmed via reading `ensure_match_transaction_info` and its callers) rather than an exploit chain that can be demonstrated without executing a live replay/restore against a deliberately divergent state tree (e.g., a modified `AptosDB` JMT implementation or corrupted backup archive) and observing that `db-tool replay-verify`/`replay_on_archive` reports success despite a wrong `state_checkpoint_hash`. I could not fully verify how far this would propagate under the newer `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / hot-state feature flags, since those are marked experimental in some contexts and a full trace through `execution_output.rs` / `state_checkpoint_output.rs` under all feature-flag combinations was not completed within this investigation — the finding rests specifically on the confirmed unconditional absence of `state_checkpoint_hash` comparison in `ensure_match_transaction_info` and its use as the sole check in replay/restore tooling.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-397)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L238-245)
```rust
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
