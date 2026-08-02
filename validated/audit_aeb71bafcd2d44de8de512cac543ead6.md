### Title
`ensure_match_transaction_info` skips checkpoint-hash validation during chunk sync / replay verification - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by chunk executor commit paths and replay-verify tooling to confirm that a `TransactionOutput` produced by local execution matches the `TransactionInfo` that was authenticated (via accumulator proof / ledger info) from a peer or archive. The function validates status, gas, write-set hash and event-root hash, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally-recomputed state roots.

### Finding Description [1](#0-0) 

```rust
pub fn ensure_match_transaction_info(...) -> Result<()> {
    ...
    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
```

The function checks `status`, `gas_used`, write-set hash, and event root hash, but returns `Ok(())` without ever comparing `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` fields carried on the (proof-authenticated) `TransactionInfo` against any state root the caller computed locally. This function is called from multiple integrity-sensitive call sites: [2](#0-1)  (chunk executor commit/verify path), `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `storage/db-tool/src/replay_on_archive.rs`, and `aptos-move/cli/src/commands.rs`.

Because the comparator never checks the state-checkpoint hash fields, a chunk/output whose write-set and events match but whose `state_checkpoint_hash` (the authenticated Sparse-Merkle-Tree root for the state at that version) diverges from what local execution would produce will still pass `ensure_match_transaction_info`. This is precisely the same class of defect as the referenced report — a value that is supposed to gate correctness (a checkpoint/state root) is silently not used in the comparison the invariant is meant to enforce.

### Impact Explanation
This breaks the invariant that "committed/synced state must match the correct VM result" and that "state proof... must stay bound to the right ... root," which is explicitly listed as an in-scope state-integrity impact. Concretely:
- During state-sync/chunk execution (`execution/executor/src/chunk_executor/mod.rs`) and replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`), a peer or archive could supply a `TransactionOutputListWithProof`/output where the write-set and events are accepted, but the associated state checkpoint root (main state, hot state, or native-position state) is wrong or stale, and this validator will not catch it.
- Replay verification (used to detect state divergence / silent forks between full nodes) could report success even though the locally-computed state root diverges from the authenticated one, defeating exactly the tool whose purpose is catching hard-fork-only divergence during replay.

### Likelihood Explanation
The gap is unconditional (not behind a feature flag check in the function itself — the TODO says the check should be added "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`", but the state/hot-state hash checks are also skipped today, independent of that flag). Any code path relying on `ensure_match_transaction_info` for verifying output authenticity against checkpoint hashes is affected right now. However, I could not fully verify within the available budget whether the actual state root is independently validated elsewhere in each of the four call sites (e.g., whether `chunk_executor` cross-checks the SMT root through a separate mechanism such as `DoStateCheckpoint`'s "known hash" comparisons before/after calling this function). The comment itself, written by the code owners, confirms the gap is real and consciously left as a TODO, which raises confidence that it is a live, acknowledged deficiency, but its ultimate exploitability depends on whether other checks in the same call chain compensate for it.

### Recommendation
Add the checkpoint-hash comparisons to `ensure_match_transaction_info`: after write-set/event checks, verify `self.write_set` (or the executed output) produces a state whose checkpoint hash (when the transaction is a checkpoint) equals `txn_info.state_checkpoint_hash()`, and equivalently for `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()` when those subsystems are enabled, using the same explicit-field comparison pattern already used for `event_root_hash` and `state_change_hash`.

### Proof of Concept
Not applicable as a runnable PoC given index-only access; the defect is demonstrated directly by the code: `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) performs `ensure!` checks for status, gas, write-set hash, and event-root hash, but the function body ends with a bare `Ok(())` after a TODO comment explicitly stating that state/hot-state and position-state checkpoint hashes are not compared. Confirming an end-to-end exploit (e.g., constructing a chunk with matching write-set/events but a forged checkpoint hash that slips past `chunk_executor`) would require running the full chunk-executor/replay pipeline, which is out of scope for static/index-only review — a Devin session with repository and test-execution access would be needed to build and run that PoC.

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
