## Analysis Result

### Title
Replay/restore verification (`TransactionOutput::ensure_match_transaction_info`) silently accepts a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
The Notional bug is a "before" vs "after" state-commitment mismatch caused by a check that doesn't cover all the state actually mutated. The Aptos-native analog is in the replay/restore verification helper `TransactionOutput::ensure_match_transaction_info`, which is the sole per-transaction integrity check used by offline replay tooling (`db-tool replay_on_archive`, `aptos-debugger`, `cli`). This function validates status, gas, write-set hash, and event-root hash against the historically-committed `TransactionInfo`, but it deliberately skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the three fields that actually attest to the resulting state root. This is explicitly acknowledged by a TODO in the code itself.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` checks:
- transaction status vs `txn_info.status()`
- gas used vs `txn_info.gas_used()`
- write-set hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()` [1](#0-0) 

but it never checks `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` — the fields that bind the transaction to the actual Sparse-Merkle/Jellyfish state root at that version. The code contains an explicit acknowledgment of this gap: [2](#0-1) 

This function is the only per-transaction correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes historical transactions and calls `ensure_match_transaction_info` to decide whether the replay matches the archived, ledger-committed `TransactionInfo`: [3](#0-2) 

The same helper is also used by `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` for equivalent replay/debug verification.

### Impact Explanation
Because state-root fields are excluded from the comparison, any divergence between the locally re-executed state (i.e., the JMT/SMT root actually produced by `do_state_checkpoint`/`do_ledger_update` on the machine doing verification) and the state root that was authoritatively committed to the chain at that version will not be detected by this check. A transaction whose write-set hash, events, gas, and status happen to match, but whose derived state root differs (e.g., due to a state-store/restore bug, an executor state-checkpoint bug, or a hot-state/position-state computation bug), will be reported as a *successful* replay match. This directly matches the in-scope impact "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Storage schemas, replay paths, and restore helpers must not reinterpret committed data into a different ledger state" — replay-verify tooling that operators rely on to catch exactly this class of divergence has a blind spot for the state-root fields.

### Likelihood Explanation
This is not a hypothetical: the comment in the code explicitly states this can happen ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"), and flags it as something that must be fixed before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. The gap is triggered whenever a state-root-affecting bug exists elsewhere in the storage/executor stack (e.g., in `do_state_checkpoint.rs`'s position/hot-state summary computation) — precisely the class of bug this tool is meant to catch. Because the check silently passes, such a bug could go undetected through the standard replay-verify CI/ops workflow.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever the caller has (or can compute) the corresponding state root for that version, rather than only comparing write-set/event/gas/status. At minimum, gate enablement of `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and any reliance on `db-tool replay_on_archive` for state-root correctness until this validation is added, as the existing TODO comment recommends.

### Proof of Concept
1. Introduce (or trigger) any divergence solely in state-root computation — e.g., a bug in `DoStateCheckpoint::compute_position_checkpoint` or hot-state checkpoint hashing — that does not change the write set, events, gas, or status of a transaction. [4](#0-3) 
2. Run `db-tool replay-on-archive` (or the `aptos-debugger`/`cli` replay path) over the affected version range.
3. `execute_and_verify` calls `ensure_match_transaction_info`, which only compares status/gas/write-set-hash/event-root-hash against the archived `TransactionInfo`. [5](#0-4) 
4. Because none of the state-checkpoint hash fields are compared, the replay is reported as matching even though the state root diverges — the tool returns `Ok(None)` (no error) for that transaction.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L21-49)
```rust
#[bon::bon]
impl DoStateCheckpoint {
    #[builder(finish_fn = build)]
    pub fn run<'a, 'db>(
        execution_output: &'a ExecutionOutput,
        parent_state_summary: &'a LedgerStateSummary,
        persisted_state_summary: &'a ProvableStateSummary<'db>,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        known_hot_state_checkpoints: Option<Vec<Option<HashValue>>>,
        parent_position_state_summary: Option<&'a LedgerWithSummary<PositionStateWithSummary>>,
        persisted_position_state_summary: Option<&'a ProvablePositionStateSummary<'db>>,
        known_position_state_checkpoints: Option<Vec<Option<HashValue>>>,
    ) -> Result<StateCheckpointOutput> {
        let _timer = OTHER_TIMERS.timer_with(&["do_state_checkpoint"]);

        let state_summary = parent_state_summary.update(
            persisted_state_summary,
            &execution_output.hot_state_updates,
            execution_output.to_commit.state_update_refs(),
        )?;

        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
```
