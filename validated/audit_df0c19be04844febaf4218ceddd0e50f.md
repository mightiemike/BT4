### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting chunk-executor replay accept transaction outputs whose state/hot-state/position-state roots diverge from the authenticated `TransactionInfo` - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used when replaying transactions from a chunk (state sync, backup restore, fast-sync, and `db-tool`'s replay-verify path) to confirm a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` stored in the ledger/accumulator. It explicitly checks `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash`, but a code comment in the function itself documents that it **does not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` compares a locally computed `TransactionOutput` against the `TransactionInfo` retrieved (with proof) from storage/backups, and is meant to guarantee that replay reproduces the exact committed ledger state. It validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set` hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()` [2](#0-1) 

It never touches `state_checkpoint_hash()`, `hot_state_checkpoint_hash()` (V1), or `position_state_checkpoint_hash()` (V1), even though these fields are part of `TransactionInfoV0`/`TransactionInfoV1` and are included in the accumulator leaf hash that anchors the ledger's Merkle proof chain: [3](#0-2) [4](#0-3) 

The comment in the code acknowledges the gap directly: replay-verify tooling (e.g. `db-tool`'s `replay_on_archive`) can report a *successful* replay even when the authenticated state (or hot-state / trading-native position-state) root diverges from what local execution actually produced, because this comparator is the sole gate used to decide "does my replayed output match the authenticated chain data."

This mirrors the report's bug class: an optional/secondary code path (here, checkpoint-hash reconciliation) is silently skipped, so a valid, non-adversarial scenario (replay when `DoStateCheckpoint`/trading-native computation differs) is treated as a match when it is not — analogous to Swivel's `lend()` silently treating "no PT swap" the same as "funds accounted for," when in fact a piece of state (the premium / here, the state root) is left unreconciled.

Callers of this function include `execution/executor/src/chunk_executor/mod.rs` (chunk/state-sync replay), `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` (replay/debug tooling used for state verification against mainnet history). [5](#0-4) 

### Impact Explanation
If a locally recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` disagrees with the authenticated value recorded on-chain/in backups, `ensure_match_transaction_info` still returns `Ok(())`. Consumers that rely on this check as their sole state-integrity gate (replay-verify, chunk executor replay during fast sync/restore, debugger-based auditing) will therefore accept and commit/report state whose Sparse-Merkle/Jellyfish state root does not match the ledger's authenticated root. This is a genuine "wrong accumulator-bound proof/root accepted as valid" condition scoped to replay/restore/verification, matching the state-integrity gate's inclusion of "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong accumulator root ... accepted as valid." A divergence here would surface either as an undetected state root mismatch (silent corruption propagated through restore/fast-sync) or as a false-positive "replay succeeded" result from verification tooling that is otherwise depended upon to catch consensus/state divergences before they are mistaken for validated history.

### Likelihood Explanation
The gap is unconditional in this comparator — it doesn't depend on malicious input, only on any scenario where write-set/event/gas/status match but the state-checkpoint-related roots differ (e.g., an execution-layer bug affecting `DoStateCheckpoint`, a hot-state or "trading-native" state computation regression, or a JMT root discrepancy introduced by an unrelated code change). Given the comment explicitly flags this as a known, currently-unguarded blind spot tied to the yet-to-be-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, the likelihood of the check being relied upon in its current (incomplete) form is high for any team using replay-verify as a correctness oracle. However, I could not fully trace, within this session, how much practical checkpoint-hash mismatch could occur from an unprivileged path in the current codebase (i.e., whether other checks elsewhere in the chunk executor pipeline redundantly catch state-root mismatches before or after this call), so I cannot assert this is independently exploitable to corrupt mainnet state versus being a verification-only gap.

### Recommendation
Extend `ensure_match_transaction_info` to also assert that the locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present, based on the on-chain feature flags active at that version) match the corresponding fields on `txn_info`, returning an error on mismatch just as is done for `write_set_hash` and `event_root_hash`, before enabling/relying on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production tooling.

### Proof of Concept
Not applicable as a runnable PoC — the finding is a static code-review observation of a documented, self-acknowledged gap in `ensure_match_transaction_info`. I was not able to fully confirm, within the given tool budget, whether an independent guard elsewhere in the chunk-executor commit path (e.g., in `DoStateCheckpoint`'s own recomputation or an accumulator-root comparison performed separately from this function) already prevents undetected state-root divergence from being committed to durable storage. This should be verified before treating the impact as anything beyond "verification-tooling can under-report divergence," since the comment itself frames it as a replay-verify observability gap rather than a proven path to on-chain commit of incorrect state.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L1-16)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

#![forbid(unsafe_code)]

use crate::{
    logging::{LogEntry, LogSchema},
    metrics::{APPLY_CHUNK, CHUNK_OTHER_TIMERS, COMMIT_CHUNK, CONCURRENCY_GAUGE, EXECUTE_CHUNK},
    types::{
        executed_chunk::ExecutedChunk, partial_state_compute_result::PartialStateComputeResult,
    },
    workflow::{
        do_get_execution_output::DoGetExecutionOutput, do_ledger_update::DoLedgerUpdate,
        do_state_checkpoint::DoStateCheckpoint,
    },
};
```
