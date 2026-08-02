## Finding

### Title
`ensure_match_transaction_info` silently skips state-checkpoint hash validation, letting replay/verify tooling accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verify tooling (`aptos-debugger`, `aptos-move/cli`, and `db-tool`'s `replay_on_archive`) to check that a locally re-executed `TransactionOutput` matches the trusted, accumulator-proven `TransactionInfo` pulled from a backup/archive. It validates status, gas, write-set hash, and event root hash, but explicitly never validates `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that authenticate the resulting Merkle state root. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` checks four things against `txn_info`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. The function's own trailing comment admits the gap:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution.
``` [2](#0-1) 

Critically, in the normal `chunk_executor` commit path (`execution/executor/src/chunk_executor/mod.rs::update_ledger`), the state-checkpoint hash *is* independently re-validated: `known_state_checkpoints` (taken from the trusted `TransactionInfo`s) is compared against the freshly computed root in `DoStateCheckpoint::run` / `get_state_checkpoint_hashes`, which `ensure!`s `known[idx] == Some(computed_last_checkpoint_hash)`. [3](#0-2) [4](#0-3) 

However, the standalone replay/verify tools do **not** go through `DoStateCheckpoint`/`DoLedgerUpdate` at all — they only call `execute_block` and then `ensure_match_transaction_info` per transaction:

```rust
let executed_outputs = executor.execute_block(...)?;
...
executed_outputs[idx].ensure_match_transaction_info(
    version, &expected_txn_infos[idx], Some(&expected_writesets[idx]), Some(&expected_events[idx]),
)
``` [5](#0-4) 

The same pattern of relying solely on `ensure_match_transaction_info` (without any state-checkpoint recomputation/comparison) is used by `execution/executor/src/chunk_executor/mod.rs::verify_execution` (backup transaction restore's replay-from-version verification path) and by `aptos-move/aptos-debugger` and `aptos-move/cli`: [6](#0-5) 

Since none of these code paths independently recompute and compare the JMT/state root against `state_checkpoint_hash`, a divergence between the VM-recomputed state (e.g., from a storage-schema bug, a JMT/state-tree computation bug, or any non-determinism introduced by a hard fork) and the archived, accumulator-proven `state_checkpoint_hash` will go undetected: `ensure_match_transaction_info` returns `Ok(())` even though the actual post-transaction state root differs from the one committed to and proven by the ledger.

### Impact Explanation
Replay-verify and state-restore verification are integrity backstops meant to detect when a node's (or an operator's) recomputed ledger state diverges from the authenticated chain history. If these tools report success despite a state-root divergence, operators and infrastructure relying on `db-tool replay_on_archive`, backup replay-verification (`VerifyExecutionMode`), or `aptos-debugger`/`aptos-move/cli` replay for auditing archived history will falsely trust a corrupted or hard-fork-diverged state as validated. This directly matches the state-integrity gate criterion "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "committed state that differs from the correct VM result... accepted as valid," since the authenticated `state_checkpoint_hash` — the value that should bind local computation to the proven ledger version/root — is never checked in these paths.

### Likelihood Explanation
This isn't a hypothetical: the gap is explicit and acknowledged in the code's own TODO comment, and it applies to `state_checkpoint_hash`/`hot_state_checkpoint_hash` today (not merely a not-yet-enabled `position_state_checkpoint_hash` feature). Any bug in state-tree computation, sharded state, or hot-state logic that produces a wrong root but a correct write-set hash (write-set hash only covers the value writes, not the resulting Merkle root) would be silently accepted by every caller of `ensure_match_transaction_info` that doesn't independently run `DoStateCheckpoint`.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash` (and `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when applicable) against an independently recomputed root, or require all callers (`replay_on_archive`, `aptos-debugger`, `aptos-move/cli`, chunk executor's `verify_execution`) to run the same `DoStateCheckpoint` logic used by the main commit path before declaring a replay verified.

### Proof of Concept
1. Construct/replay a transaction whose write-set hash and events match the archived `TransactionInfo` (i.e., `state_change_hash`/`event_root_hash` unchanged) but whose actual resulting Merkle state root differs from `state_checkpoint_hash` (e.g., by injecting a state-tree/hot-state computation bug into the executor used for replay only).
2. Run `db-tool replay_on_archive` (or `aptos-debugger`/`aptos-move/cli` replay) over the affected version range.
3. Observe that `execute_and_verify` / `ensure_match_transaction_info` reports success at that version because it never compares against `state_checkpoint_hash`, even though the recomputed state root diverges from the one committed on-chain. [7](#0-6)

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

**File:** storage/db-tool/src/replay_on_archive.rs (L373-406)
```rust
        let executed_outputs = executor
            .execute_block(
                &txns_provider,
                &self
                    .arc_db
                    .state_view_at_version(current_version.checked_sub(1))?,
                BlockExecutorConfigFromOnchain::new_no_block_limit(), // TODO(HotState): will need to incorporate some features.
                TransactionSliceMetadata::Chunk {
                    begin: *current_version,
                    end: *current_version + cur_txns.len() as u64,
                },
            )
            .map(BlockOutput::into_transaction_outputs_forced)?;
        assert_eq!(executed_outputs.len(), cur_txns.len());

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
