## Finding: Replay-Verify Path Skips State/Hot-State/Position-State Checkpoint Hash Validation

### Title
Replay-Verify and Debugger Tooling Accept Divergent State Roots as Valid Because `ensure_match_transaction_info` Never Checks Checkpoint Hashes - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info`, the authoritative comparator used by the chunk-executor's replay-verify path, `db-tool`'s `replay_on_archive`, and the `aptos-debugger`/CLI execute-past-transactions tooling, validates only status, gas, write-set hash, and event root hash against the trusted `TransactionInfo`. It never checks `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, so any of these state roots can silently diverge from the authenticated ledger value while the tool reports a successful, verified replay.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  compares a freshly-computed `TransactionOutput` against a trusted `TransactionInfo` (itself authenticated by an accumulator/ledger-info proof) but only asserts equality of `status`, `gas_used`, the write-set hash (`state_change_hash`), and the event root hash. The function's own trailing comment documents the gap explicitly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This function is the sole verification step in the chunk-executor's `verify_execution` (used by replay-verify against backups/archives), which re-executes a transaction range and checks the result against on-disk `TransactionInfo`s without ever invoking `DoStateCheckpoint` or comparing computed Merkle/JMT/position roots: [3](#0-2) 

The same comparator is used by `db-tool`'s `replay_on_archive` and by the `aptos-debugger`'s mismatch-printing helper, both of which are the mainnet-facing tools operators run to confirm a downloaded backup or a re-executed transaction range produces the exact chain state: [4](#0-3) 

By contrast, the normal (non-replay) ledger-update path does check computed state/hot-state/position checkpoint hashes against `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` inside `DoStateCheckpoint::run`, e.g. in `chunk_executor::update_ledger`: [5](#0-4) 

That means the omission is specific to the audit/replay-verification tooling path (`verify_execution`), not the live-commit path — the state root is genuinely enforced when a chunk is *committed*, but the *offline verification* tools that are supposed to independently attest "this backup/replay reproduces the exact authenticated ledger" do not check the state root at all.

### Impact Explanation
Any bug in state-root computation logic (main JMT root, hot-state root, or the newer position/"trading-native" state root gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that corrupts the committed state tree would go completely undetected by `replay_on_archive`, the executor's chunk `verify_execution`, and `aptos-debugger`'s output-mismatch diagnostics. These tools are explicitly relied upon as the last line of defense to catch state-computation divergences during backup verification and post-mortem incident analysis. A silent divergence in `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` is exactly the "committed state differs from correct VM result" and "wrong ... state proof accepted as valid" class of impact called out as in-scope, since these tools would falsely certify a corrupted or forked state root as a verified match against the authenticated `TransactionInfo`.

### Likelihood Explanation
This is not a remotely-triggerable single-transaction exploit; it requires an underlying state-computation bug (e.g., a JMT/hot-state/position-tree divergence bug, malicious backup source, or non-deterministic hot-state promotion logic) to exist and go unnoticed specifically because the verification tooling has this blind spot. The likelihood of the tooling gap being exploited scales with how much operators/auditors rely on `replay_on_archive`/`aptos-debugger` as their sole validation of backups and replays — which is a realistic and documented use case, and the gap is already flagged in-repo by the authors themselves as needing to be closed before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, indicating it is a known but currently-unaddressed weakness.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (when hot-state root computation is enabled), and `position_state_checkpoint_hash()` (when `compute_trading_native_state_roots` is enabled) against locally recomputed values, mirroring the checks already performed via `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` in `DoStateCheckpoint::run`. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or hot-state-root verification is relied upon in production, as the code comment itself indicates.

### Proof of Concept
Conceptual PoC (no live exploitation possible without an existing state-root computation bug):
1. Introduce (or trigger via an existing latent bug) a divergence in computed `state_checkpoint_hash` for a given transaction range (e.g., a corrupted JMT node during restore, or a hot-state promotion bug).
2. Run `db-tool`'s `replay_on_archive` or `chunk_executor`'s `verify_execution` (replay-verify) against the backup/archive covering that range.
3. Observe `ensure_match_transaction_info` returns `Ok(())` because it only checks status/gas/write-set hash/event root hash — the corrupted state root is never compared, and the tool reports the replay as successfully verified despite the ledger state being wrong.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L374-413)
```rust
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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
    }
```
