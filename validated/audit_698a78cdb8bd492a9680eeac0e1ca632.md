## Analysis: Replay-verification tooling silently accepts a divergent state root

### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash comparison, letting replay-verify tooling accept a corrupted state-commitment root - (File: `types/src/transaction/mod.rs`)

### Summary
`db-tool`'s `replay_on_archive` verifier and the `aptos-move/cli` transaction-replay command both re-execute a historical transaction and validate the freshly computed `TransactionOutput` against the archived/authenticated `TransactionInfo` via `TransactionOutput::ensure_match_transaction_info`. That function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents with a `TODO(trading-native)` comment.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates 4 fields (status, gas_used, write_set_hash, event_root_hash) but the comment at lines 2197-2202 states plainly: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole correctness gate for two independent replay/verification tools:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and calls `ensure_match_transaction_info` per transaction [2](#0-1) .
- `aptos-move/cli`'s transaction replay command, which does the same comparison before printing a transaction summary [3](#0-2) .
- `execution/executor/src/chunk_executor/mod.rs`'s `verify_execution`, used during `VerifyExecutionMode`-driven state-sync/backup verification, which calls the exact same function [4](#0-3) .

By contrast, the normal chunk-executor commit path (`ReplayChunkVerifier` / `StateSyncChunkVerifier`) uses `LedgerUpdateOutput::ensure_transaction_infos_match`, which does a full `TransactionInfo == TransactionInfo` comparison (covering all fields, including checkpoint hashes) [5](#0-4) . So the gap is confined to the `ensure_match_transaction_info`-based tools (db-tool `replay_on_archive`, CLI replay, and `verify_execution`'s explicit chunk-executor call), not the primary consensus/commit path.

### Impact Explanation
`state_checkpoint_hash` is the authenticated Sparse-Merkle-Tree root committing the entire world state at a checkpoint version; `TransactionInfoV1` additionally carries `hot_state_checkpoint_hash` and (per the comment) a forthcoming `position_state_checkpoint_hash` for the "trading-native" state root. These are exactly the "wrong accumulator root ... accepted as valid" and "hard-fork-only divergence during ... replay" cases called out in the state-integrity gate. If any of these state roots diverge from local re-execution — due to a bug in a new VM/state feature (e.g. hot-state promotion logic or the upcoming trading-native position tree), a storage schema migration bug, or a subtle non-determinism — `replay_on_archive` and the CLI replay tool will both report success, because the only fields compared are write_set_hash/event_root_hash/gas/status. Operators, auditors, and node runners rely on these tools specifically to catch state-root divergence during archive replay and pre-mainnet validation of the very state-commitment features (hot state, position/trading-native state) that are actively being rolled out. A silent pass masks a genuine ledger-state corruption or hard-fork bug instead of surfacing it before it reaches production consensus.

### Likelihood Explanation
This is not a hypothetical: the gap is triggered automatically any time `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or hot-state root features) are exercised during replay, and the code comment confirms the authors are aware the checkpoint-hash comparison must be added "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." No malicious actor input is needed — normal replay-verify usage against any chain history that includes a checkpoint transaction silently loses checkpoint-root fidelity. The severity is amplified because this is precisely the safety net meant to catch state-commitment divergence before/during a hard fork or feature rollout.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields recomputed from the re-executed `ExecutionOutput`/`StateCheckpointOutput` (the same hashes assembled in `DoLedgerUpdate::assemble_transaction_infos`, see [6](#0-5) ), rather than deferring this to a follow-up TODO. At minimum, gate the rollout of `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and hot-state-root features on landing this check, since `replay_on_archive` and the CLI replay path are explicitly documented as consumers of the currently-incomplete comparator.

### Proof of Concept
1. On an archive/replica DB, commit a transaction that is a state checkpoint, where local re-execution's computed `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` under `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) legitimately differs from the value stored on disk (e.g. from a state-tree/hot-state computation bug affecting only the checkpoint-hash path, not the write-set/events).
2. Run `db-tool replay-on-archive --start-version <v> --end-version <v>` (or the CLI's `replay --txn-id <v>`), which calls `execute_and_verify` → `TransactionOutput::ensure_match_transaction_info` [2](#0-1) .
3. Because `ensure_match_transaction_info` never compares checkpoint hashes [7](#0-6) , the tool reports the replay as matching/successful even though the state-commitment root diverges from the archived, authenticated one — masking the exact class of bug the tool exists to detect.

**Note on limitations:** I was not able to inspect the full body of `storage/db-tool/src/replay_on_archive.rs` lines 90-315 or confirm whether any other, separate check independently validates checkpoint hashes elsewhere in that binary (e.g., a distinct state-tree comparison pass outside `ensure_match_transaction_info`); the index only returned partial slices of that file. If such an independent check exists, it would narrow (but likely not eliminate, given the explicit code comment) this finding's impact.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-397)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
```

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** execution/executor/src/chunk_executor/mod.rs (L692-697)
```rust
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
```

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
        Ok(())
    }
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
```rust
                let txn_info = if transaction_info_v1 {
                    TransactionInfo::builder_v1()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .maybe_hot_state_checkpoint_hash(
                            hot_state_checkpoint_hashes.and_then(|hot| hot[i]),
                        )
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .maybe_position_state_checkpoint_hash(
                            position_state_checkpoint_hashes.and_then(|p| p[i]),
                        )
                        .build()
                } else {
                    TransactionInfo::builder_v0()
                        .transaction_hash(txn.committed_hash())
                        .state_change_hash(write_set_hash)
                        .event_root_hash(event_root_hash)
                        .maybe_state_checkpoint_hash(state_checkpoint_hash)
                        .gas_used(txn_output.gas_used())
                        .status(status)
                        .maybe_auxiliary_info_hash(auxiliary_info_hash)
                        .build()
                };
```
