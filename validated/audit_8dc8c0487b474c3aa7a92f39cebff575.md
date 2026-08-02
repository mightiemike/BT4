### Title
`ensure_match_transaction_info` skips state/hot-state/position checkpoint-hash validation, letting `replay-verify` and backup restore accept a locally-recomputed execution that diverges from the committed state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness gate used by backup restore/replay-verify tooling (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`, `storage/db-tool/src/replay_on_archive.rs`, `storage/db-tool/src/replay_verify.rs`) and by the executor's own `chunk_executor::verify_execution` path to confirm that locally re-executed transaction outputs match the already-committed, ledger-info-authenticated `TransactionInfo`. The function checks status, gas, write-set hash, and event root hash, but — per its own inline TODO — deliberately skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  compares a freshly re-executed `TransactionOutput` against the corresponding archived/committed `TransactionInfo`, but only verifies:
- execution status
- gas used
- `write_set` hash vs `state_change_hash`
- event root hash

It explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, as noted in its own comment: [2](#0-1) 

These checkpoint hashes are the Sparse-Merkle/Jellyfish state roots (and, once `HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled, hot-state and native-position roots) that are folded into `TransactionInfo` and hashed into the transaction accumulator that the network's `LedgerInfo` signatures ultimately authenticate [3](#0-2) . This function is the only place these locally-recomputed values are cross-checked against the persisted/backed-up record in three critical integrity tools:
- Backup restore's transaction stage [4](#0-3) 
- `db-tool replay-verify` / `replay-on-archive`, whose entire purpose is to catch VM/state divergence by re-executing archived history and comparing against the trusted, previously-committed `TransactionInfo` [5](#0-4) 
- `ChunkExecutor::verify_execution`, used during executor-driven verify-execution flows [6](#0-5) 

Because the state-root fields are skipped, a locally-recomputed state whose Merkle root diverges from the archived/committed root (e.g., due to a state-computation bug, a JMT proof/restore inconsistency, or corrupted/incorrectly migrated snapshot data reaching this comparison point) will pass `ensure_match_transaction_info` as long as write-set bytes, gas, status, and events happen to match. The write-set hash check only proves the *raw write ops* match; it does not prove the *resulting Merkle tree root* the network actually committed to matches what local re-execution computes.

### Impact Explanation
This breaks the "committed state must not silently diverge from correct VM result" invariant for exactly the tooling whose job is to detect that divergence: replay-verify and restore/replay flows are the last line of defense used by node operators and infra to detect execution/state-computation bugs, hard-fork-only divergences, or corrupted archives before they propagate. If a state-root computation bug exists elsewhere (e.g., in hot-state or JMT logic) that produces a different `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` while leaving the write-set encoding, gas, status and events unchanged, `replay-on-archive` and restore verification will report success even though the locally materialized state diverges from the network's committed ledger state — masking a hard-fork-class bug rather than surfacing it. This is precisely the "Hard-fork-only divergence during commit, replay, restore, or proof verification" impact category called out in the task's gate.

### Likelihood Explanation
The gap is acknowledged in-code via the TODO, which states it should be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" [2](#0-1) , implying the feature (and thus the concretely dangerous scenario of position/hot-state root divergence) is not yet fully live on mainnet, which lowers immediate exploitability. However, the missing `state_checkpoint_hash` check applies unconditionally today, independent of that feature flag, for every V0/V1 `TransactionInfo` that carries a checkpoint hash. Any bug causing state-root divergence without altering the write-set bytes (a realistic class of bug in Merkle/JMT computation, hashing of resource groups, or snapshot restore logic) would go undetected by this specific safety net today, on any node running `replay-verify`/backup validation.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s recomputed state checkpoint hash(es) (state root, hot-state root, and position-state root when applicable) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever these are present in the `TransactionInfo` variant, mirroring the strictness already applied to write-set and event hashes. This closes the gap before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any state-root producing feature) is enabled and restores the tool's stated purpose of catching state-computation divergence during replay/restore.

### Proof of Concept
1. Enable/construct a scenario (or introduce a synthetic bug) where local re-execution of a transaction produces the same write-set bytes, gas usage, status, and events as the archived/committed transaction but a different Sparse-Merkle-Tree state root (e.g. by altering how the state snapshot is computed while leaving the same write ops).
2. Run `db-tool replay-on-archive` (`storage/db-tool/src/replay_on_archive.rs`, `execute_and_verify` at lines 349-415) or the backup-cli restore/replay-verify coordinator over the affected version range.
3. Observe that `TransactionOutput::ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) returns `Ok(())` because it never compares `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, even though the locally computed state root differs from the one embedded in the committed `TransactionInfo`/accumulator — i.e., the tool reports a successful replay despite a genuine state-commitment divergence.

*Note: I was unable to fully trace every downstream consumer of `chunk_executor::verify_execution` within the indexed portion of the codebase (some state-sync call sites were only partially visible), so I cannot conclusively confirm whether this gap is also reachable in a live validator/fullnode's state-sync-driven verify-execution mode versus purely offline `db-tool`/backup-cli usage. Given index-size limits, some file contents may not be available; a full Devin session could confirm the complete call graph and whether this can affect a running node's automatic verification rather than only manually invoked tooling.*

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-123)
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
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
```

**File:** storage/backup/backup-cli/src/backup_types/transaction/restore.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L692-706)
```rust
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
