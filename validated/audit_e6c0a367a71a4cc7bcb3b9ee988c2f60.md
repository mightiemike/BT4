### Title
Replay-verify tooling silently ignores state/hot-state/position checkpoint hashes, allowing a divergent committed state root to pass as "verified" - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticity check used by offline replay/verification tools (`db-tool`'s `replay_on_archive`, `aptos-debugger`, and the Move CLI) to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` committed on an already-signed ledger. The function checks status, gas, write-set hash, and event-root hash, but explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — fields that are part of `TransactionInfo` and are covered by the ledger's authenticated accumulator/quorum-cert signature.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates only four properties between the locally computed `TransactionOutput` and the authenticated `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. It never touches `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()` (V1 only), or `txn_info.position_state_checkpoint_hash()` (V1 only), even though these fields are part of the `TransactionInfo` enum that is itself hashed into the transaction accumulator and thus signed by validator quorum certificates, per the struct definitions at [2](#0-1) .

This gap is explicitly acknowledged in a code comment immediately following the function body: [3](#0-2) 

The function is consumed by tools whose entire purpose is to detect state-computation divergence:
- `storage/db-tool/src/replay_on_archive.rs` — the archive-node replay-verify tool, which re-executes historical transactions and is meant to catch any divergence from the authenticated ledger.
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` — used to debug/replay mainnet transactions.
- `aptos-move/cli/src/commands.rs` — used in local replay/simulation workflows.

Because the state checkpoint hashes (main state root, hot-state root, and the newer native-position state root gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, defined at [4](#0-3) ) are silently excluded from the comparison, a locally re-executed state that has a correct write-set/event/gas/status but a *different* Merkle state root (e.g., due to a JMT/state-summary bug, an execution non-determinism, or a storage bug affecting derived state) will be reported by these tools as "matching" the authenticated `TransactionInfo`.

By contrast, the actual consensus/commit-time integrity check does perform this validation: `DoStateCheckpoint::run` in [5](#0-4)  and the chunk-executor's `update_ledger` in [6](#0-5)  both recompute and compare `known_state_checkpoints`/`known_hot_state_checkpoints`/`known_position_state_checkpoints` against freshly computed roots. So the committed ledger state itself is protected during normal consensus execution and state-sync chunk application. The vulnerability is isolated to the *offline replay-verification* code path that reuses `ensure_match_transaction_info` as its sole correctness oracle.

### Impact Explanation
This breaks the "authenticated API/proof-bearing response must stay bound to the right ledger version, root, and object" invariant for offline auditing: replay-verify tooling (used to detect state divergence on archive nodes, in debugging, and potentially in release-verification pipelines) can report success even when the locally computed state checkpoint root — main state root, hot-state root, or native-position root — diverges from the root actually committed and quorum-certified on-chain. This defeats the primary purpose of `replay_on_archive`: catching state-computation bugs or storage corruption post-hoc. A real state-divergence bug (execution non-determinism, storage bug, or migration bug affecting only the JMT/state summary but not the write-set bytes) would go undetected by this tool, delaying detection of a genuine hard-fork-class divergence. This does not itself corrupt live consensus-committed state (which is separately protected in `do_state_checkpoint.rs`), so I classify it as a High-severity proof/verification-integrity gap in tooling rather than a Critical live-state-corruption bug.

### Likelihood Explanation
The condition is not attacker-triggered; it manifests whenever there is a real (even unprivileged, environment- or bug-induced) discrepancy between locally recomputed state roots and the authenticated ones, while write-set bytes, gas, events and status happen to match — a plausible scenario for narrowly-scoped storage/state-summary bugs. Given that `state_checkpoint_hash` checks are absent unconditionally (not just under an unstable feature flag) for V0 `TransactionInfo`, which is the default/current format, this gap is live in the current replay tooling today, independent of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `txn_info.state_checkpoint_hash()` against the state root computed by the replay/debug tool for checkpoint transactions (comparing `None`/`Some` presence and the hash value when present), and similarly validate `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when the corresponding features are enabled for that version. At minimum, callers in `replay_on_archive.rs`, `aptos_debugger.rs`, and `cli/commands.rs` should independently assert the state root at checkpoint boundaries, rather than relying solely on this comparator.

### Proof of Concept
1. `db-tool replay-on-archive` (or `aptos-debugger`) re-executes a transaction range against an archive DB and calls `TransactionOutput::ensure_match_transaction_info` per transaction to assert equivalence with the authenticated `TransactionInfo` from the source DB, per the call site in [7](#0-6)  (import of `TransactionInfo` and use of the verify path).
2. Introduce (or encounter) a divergence purely in state-root computation for a checkpoint transaction — e.g., a state-summary/JMT bug that changes the computed `state_checkpoint_hash` without changing the write-set bytes, events, gas, or status.
3. `ensure_match_transaction_info` computes and compares only `status`, `gas_used`, `write_set_hash`, and `event_root_hash` ( [8](#0-7) ); since these all match, the function returns `Ok(())` even though `state_checkpoint_hash` differs from the authenticated one.
4. The replay-verify tool reports the transaction as successfully verified, masking a real state-root divergence that should have been flagged as a hard-fork-class integrity failure.

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-206)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L44-83)
```rust
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

**File:** storage/db-tool/src/replay_on_archive.rs (L1-41)
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
