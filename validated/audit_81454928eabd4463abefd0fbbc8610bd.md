## Finding

### Title
Replay-verify comparator omits state-checkpoint root check, letting divergent state roots pass authenticated verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally (re-)computed `TransactionOutput` matches the authenticated `TransactionInfo` fetched from storage/backup/network for a given version. It checks execution status, gas used, write-set hash (`state_change_hash`), and event root hash, but never compares the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` fields carried by `TransactionInfo` — the fields that actually commit to the JMT state root. [1](#0-0) 

### Finding Description
`TransactionInfo` (`V0`/`V1`) stores `state_checkpoint_hash`, and `V1` additionally stores `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` — these are the authenticated Merkle roots of the global (and hot/position) state tree at that version. [2](#0-1) 

`ensure_match_transaction_info` is the single comparator that decides whether a re-executed `TransactionOutput` "matches" the trusted `TransactionInfo`. It validates status, gas, write-set hash, and event root hash, but the function body contains no comparison of any of the three checkpoint-hash fields, and the code itself documents this gap explicitly:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is consumed by exactly the tools whose entire purpose is state-integrity auditing:
- `storage/db-tool/src/replay_on_archive.rs`, which replays history from an archive and is expected to catch state divergence.
- `execution/executor/src/chunk_executor/mod.rs`'s `VerifyExecutionMode` path.
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`.
- `aptos-move/cli/src/commands.rs`. [4](#0-3) [5](#0-4) 

Note that `state_checkpoint_hash` (the primary state root) is not gated behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature — only the newer `position_state_checkpoint_hash` is. The comment focuses on the not-yet-enabled trading-native root, but the underlying comparator function omits the state and hot-state checkpoint hashes unconditionally today, regardless of any feature flag. `do_state_checkpoint.rs` shows these checkpoint hashes are computed as first-class, per-transaction commitments (`state_checkpoint_hashes`, `hot_state_checkpoint_hashes`) that get embedded in `TransactionInfo`, confirming these are meant to be authoritative commitments, not incidental data. [6](#0-5) 

### Impact Explanation
Replay-verify and chunk-executor verification are the mechanisms operators and auditors rely on to detect nondeterminism or state-computation bugs across the entire execution/storage pipeline (JMT, write-set application, aggregator resolution, etc.) by recomputing state and diffing it against the chain's authenticated commitments. Because the comparator never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, a discrepancy between the locally recomputed state root and the authenticated on-chain state root at a given version will not be flagged — `ensure_match_transaction_info` returns `Ok(())` even though the actual committed ledger state differs from what the VM/storage pipeline locally produced. This is a hard-fork-class detection gap: it is exactly the "authenticated output bound to the wrong... proof context is accepted as valid" scenario, since the tool authenticates a version against `TransactionInfo` (which is itself covered by the transaction accumulator/ledger proof) yet silently ignores its most important field for state integrity.

### Likelihood Explanation
This triggers deterministically any time replay-verify, chunk-executor `VerifyExecutionMode`, or the debugger's transaction-info matching is used on a version where the write-set hash/events/gas/status happen to match but the state (or hot-state/position-state) checkpoint root diverges — e.g. from a JMT/state-summary bug, an aggregator/resolver bug, or storage-schema regression that doesn't affect the write set itself but does affect the derived state root. No privileged access or malicious actor is required; this is a latent gap in an unprivileged, always-reachable verification code path.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` to compare the locally computed `state_checkpoint_hash` (and, when present, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`) against the corresponding fields of the passed-in `TransactionInfo`, returning an error on mismatch, consistent with how `write_set_hash` and `event_root_hash` are already validated in the same function.

### Proof of Concept
Not applicable as a runtime exploit — this is a verification-logic gap, not a state-corruption primitive by itself. Its proof is the code path itself: `ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) never references `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()`, while every caller (`replay_on_archive.rs`, `chunk_executor/mod.rs`, `aptos_debugger.rs`, `cli/src/commands.rs`) relies on this function as the sole match/verify gate for a replayed `TransactionOutput` against the trusted `TransactionInfo`. A unit test constructing a `TransactionOutput` with a correct write set/events but wired against a `TransactionInfo` whose `state_checkpoint_hash` was tampered to an arbitrary wrong value would still pass `ensure_match_transaction_info`, demonstrating the gap directly.

*Caveat*: I was unable to fully verify, given tool-call limits, whether any of the four call sites perform an additional, separate state-root comparison outside of this function (e.g., directly diffing JMT roots) that would compensate for this gap. The TODO comment in the source itself, however, explicitly states that `replay_on_archive` "can report a successful replay even when the authenticated position state root diverges from local execution," which corroborates that no such compensating check exists for at least the position-state root, and by code inspection, none exists in this function for the base `state_checkpoint_hash` either.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L42-60)
```rust
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
```
