### Title
`TransactionOutput::ensure_match_transaction_info` omits state/hot-state/position checkpoint hash verification, allowing replay/restore consistency checks to silently accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the invariant-check function used by replay and chunk-execution code paths to confirm that a locally re-executed/recomputed `TransactionOutput` matches an authenticated `TransactionInfo` (the object actually committed into the transaction accumulator and signed via `LedgerInfoWithSignatures`). The function validates status, gas used, write-set hash, and event root hash, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally computed state-checkpoint root. [1](#0-0) 

### Finding Description
The function is meant to be the integrity gate that ties a re-executed `TransactionOutput` to the trusted, previously-authenticated `TransactionInfo`. It checks `status`, `gas_used`, `write_set_hash`, and `event_root_hash`, but a comment in the code itself documents that the state checkpoint fields are not compared: [2](#0-1) 

`state_checkpoint_hash` (and the newer `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` introduced for the "trading-native"/position state feature gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is the commitment binding a given block/version's locally-materialized Jellyfish Merkle / SMT root into the `TransactionInfo` that is hashed into the transaction accumulator and ultimately signed by validators. Skipping it means the equality check between "what I just computed by re-applying write sets locally" and "what the authenticated `TransactionInfo` claims" is incomplete for exactly the field that binds state-tree contents to the proof structure.

This function is used by:
- `execution/executor/src/chunk_executor/mod.rs` (chunk/fast-sync replay path validating output against a provided `TransactionInfo`)
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (replay-verify / debug tooling used to detect state divergence, e.g. `replay_on_archive`) [3](#0-2) 

### Impact Explanation
Because the checkpoint-hash fields are excluded from the comparison, replay-verify tooling (and any chunk-execution consistency check relying on this function) can report a **successful match** even when the locally computed state-checkpoint root (state tree / hot-state tree / position-state tree) diverges from the authenticated root baked into the signed `TransactionInfo`. This is precisely the class of "hard-fork-only divergence during commit/replay/restore" the state-integrity gate calls out: an execution or storage bug that corrupts the locally materialized state tree would go undetected by this invariant check, allowing a node to continue operating (and reporting) on a divergent ledger state without any invariant-check failure at this call site.

### Likelihood Explanation
The gap is real and self-documented in the source itself as an intentional-but-unaddressed TODO tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. I was not able to fully confirm, within the available tooling, whether an independent redundant check (e.g., accumulator-root comparison performed elsewhere in the chunk executor or CLI replay flow) already covers this gap end-to-end, which would reduce the practical exploitability. The index also does not let me exhaustively trace every caller's post-processing to rule out a compensating control outside this function.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present) against the values computed from local re-execution, exactly as is already done for `write_set_hash` and `event_root_hash`, before the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature is enabled on mainnet.

### Proof of Concept
Not independently reproduced as a live exploit; this is based on static analysis of `ensure_match_transaction_info` in `types/src/transaction/mod.rs:2139-2204`, where the code path explicitly omits checkpoint-hash comparisons that its own inline comment flags as a required TODO before the corresponding feature can be safely enabled. Given the inability to conclusively rule out compensating checks in every caller with the tools available, this should be treated as a candidate needing manual/dynamic verification rather than a confirmed exploited-in-the-wild bug.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L1-20)
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
use anyhow::{anyhow, ensure, Result};
use aptos_executor_types::{
    ChunkCommitNotification, ChunkExecutorTrait, TransactionReplayer, VerifyExecutionMode,
};
```
