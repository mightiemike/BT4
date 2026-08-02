### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting replay/verify tooling accept a divergent state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay-verify tooling and the chunk executor to confirm that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` pulled from a backup/archive. Its own code comment documents that it deliberately does not check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against locally computed values.

### Finding Description
`ensure_match_transaction_info` compares status, gas used, write-set hash (`state_change_hash`), and event root hash between the locally produced `TransactionOutput` and the `TransactionInfo` supplied from disk/backup, but explicitly excludes the state/hot-state/position checkpoint hashes from comparison: [1](#0-0) 

The comment block makes the gap explicit: [2](#0-1) 

This function is used across multiple integrity-sensitive callers, including the chunk executor's commit-time verification path and the `db-tool replay-on-archive` replay-verify utility: [3](#0-2) [4](#0-3) 

`TransactionInfoV1` now carries a `position_state_checkpoint_hash` field (a repurposed reserved field) alongside `state_checkpoint_hash` and `hot_state_checkpoint_hash`: [5](#0-4) 

Because `ensure_match_transaction_info` never re-derives and compares these checkpoint hashes against the state/hot-state/position state summaries produced by local execution (see `DoStateCheckpoint::run`, which computes `state_checkpoint_hashes`, `hot_state_checkpoint_hashes`, and `position_state_checkpoint_hashes` from the state summary): [6](#0-5) 

a replay/verify run (or chunk-executor commit-time verification) that hits a bug causing the locally computed state root, hot-state root, or position-state root to diverge from the authenticated on-chain value will not be caught by this check. The transaction, events, and gas usage will match, so `ensure_match_transaction_info` returns `Ok(())` even though the state commitment itself is wrong.

### Impact Explanation
This breaks the fundamental proof-integrity invariant that authenticated `TransactionInfo` (which is itself covered by the transaction accumulator and ultimately signed in `LedgerInfo`) must be independently reproducible and checked bit-for-bit by verification tooling. A silent divergence in `state_checkpoint_hash` (or the newer hot/position state roots) during `replay_on_archive`, chunk-executor state-sync verification, or debugger replay means:
- A consensus/executor bug causing state-root divergence (e.g., a hard-fork-only divergence, an execution or storage bug affecting the sparse Merkle tree or the new position-state summary) would go undetected by the very tooling designed to catch such divergences.
- Nodes/operators relying on `replay_on_archive` or chunk-sync verification to detect a compromised/incorrect state would get a false "match" result, allowing corrupted or forked state to be accepted as verified.

This matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong state proof accepted as valid" categories called out as in-scope, since the root cause is local (a checked-in TODO gap in the comparator) and not any privileged/malicious-actor assumption.

### Likelihood Explanation
The bug is a static verification gap, not a probabilistic race — every call to `ensure_match_transaction_info` skips checkpoint hash verification, deterministically, on every replay/verify/chunk-execution run. It would only manifest as an observable divergence when some other bug (execution, storage schema, hot-state/position-state computation) actually produces a wrong checkpoint hash, at which point this comparator fails to flag it. So while the comparator gap is always present, the security consequence depends on a second latent bug being present that would otherwise have been caught. The code's own comment ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`") indicates the developers are aware of and tracking this as a pre-requisite gap for an upcoming feature, corroborating that this is a live, acknowledged hole rather than a false positive.

### Recommendation
Extend `ensure_match_transaction_info` to accept and compare locally computed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when applicable/known) against the corresponding fields of `txn_info`, failing verification on any mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any feature relying on `position_state_checkpoint_hash`) is enabled on mainnet.

### Proof of Concept
Not directly exploitable via a single crafted transaction; the gap is demonstrated by inspection of `ensure_match_transaction_info`'s comparison logic versus `TransactionInfoV1`'s full checkpoint-hash surface:
1. Construct (or trigger) a scenario where local execution produces a correct write set/events/gas/status but an incorrect `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` (e.g., due to a bug in `DoStateCheckpoint::run` or the position-state summary path).
2. Run `replay_on_archive` or trigger chunk-executor verification against the authenticated `TransactionInfo`.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` despite the checkpoint-hash divergence, because those fields are never compared. [1](#0-0)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust

```

**File:** storage/db-tool/src/replay_on_archive.rs (L1-40)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

use anyhow::{bail, Error, Ok, Result};
use aptos_backup_cli::utils::{ReplayConcurrencyLevelOpt, RocksdbOpt};
use aptos_block_executor::txn_provider::default::DefaultTxnProvider;
use aptos_config::config::{
    HotStateConfig, StorageDirPaths, BUFFERED_STATE_TARGET_ITEMS,
    DEFAULT_MAX_NUM_NODES_PER_LRU_CACHE_SHARD, NO_OP_STORAGE_PRUNER_CONFIG,
};
use aptos_db::{backup::backup_handler::BackupHandler, AptosDB};
use aptos_logger::prelude::*;
use aptos_storage_interface::{
    state_store::state_view::db_state_view::DbStateViewAtVersion, AptosDbError, DbReader,
};
use aptos_types::{
    block_executor::{
        config::BlockExecutorConfigFromOnchain,
        transaction_slice_metadata::TransactionSliceMetadata,
    },
    contract_event::ContractEvent,
    transaction::{
        signature_verified_transaction::SignatureVerifiedTransaction, AuxiliaryInfo, BlockOutput,
        PersistedAuxiliaryInfo, Transaction, TransactionInfo, Version,
    },
    write_set::WriteSet,
};
use aptos_vm::{aptos_vm::AptosVMBlockExecutor, AptosVM, VMBlockExecutor};
use aptos_vm_environment::prod_configs::{
    set_async_runtime_checks, set_layout_caches, set_paranoid_type_checks,
};
use clap::Parser;
use rayon::{iter::ParallelIterator, prelude::IntoParallelIterator};
use std::{
    panic,
    path::PathBuf,
    process,
    sync::{atomic::AtomicU64, Arc},
    time::Instant,
};
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-83)
```rust
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

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```
