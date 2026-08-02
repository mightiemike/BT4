This confirms the candidate is well-supported and is explicitly self-documented in code with three real call sites (`chunk_executor::verify_execution`, `replay_on_archive::execute_and_verify`, and the CLI replay tool) that rely on `ensure_match_transaction_info` as the sole correctness gate.

### Title
`TransactionOutput::ensure_match_transaction_info` omits checkpoint-hash comparisons, letting replay/verification paths accept a corrupted state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the function used by chunk-execution verification and replay-verify tooling to confirm that a locally re-executed transaction output matches the authenticated `TransactionInfo` recorded on-chain. It checks status, gas used, write-set hash, and event root hash, but never compares the state checkpoint hash, hot-state checkpoint hash, or `position_state_checkpoint_hash` carried by `TransactionInfo`/`TransactionInfoV1`. This mirrors the reported bug class: a check is performed against the wrong/incomplete subject (write-set/events only) instead of the full committed-state binding (checkpoint/position roots), letting mismatches slip through undetected.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  verifies four properties of a `TransactionOutput` against a `TransactionInfo`: execution status, gas used, write-set hash (`state_change_hash`), and event root hash. It explicitly does **not** verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the actual post-commit state tree roots. This gap is called out directly in the trailing comment: `"this comparator ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"` [2](#0-1) .

This function is the actual correctness oracle in three call sites:
- `ChunkExecutor::verify_execution`, which is used during chunk-based state-sync/backup verification to confirm a locally re-executed transaction batch matches the trusted transaction infos before treating them as verified [3](#0-2) .
- `storage/db-tool`'s `replay_on_archive` verifier, whose entire job is to replay historical transactions and flag any divergence from the archived, ledger-info-signed `TransactionInfo` [4](#0-3) .
- The Move CLI's transaction-replay command, which reports pass/fail based solely on this comparison [5](#0-4) .

Because none of these call sites independently re-check the state/hot-state/position checkpoint hashes, none of them can detect a case where the write set and events reproduce correctly but the resulting Merkle/Jellyfish state root (or the newer "trading-native" position-state root) diverges — e.g. due to a state-checkpoint materialization bug, an ordering bug in applying writes, or divergent hot-state handling introduced by a future change gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (mentioned directly in the TODO).

### Impact Explanation
State checkpoint hashes are exactly the values used elsewhere in the codebase to authenticate state restore (e.g. `StateSnapshotRestoreController::run_impl` checks `state_root_hash == manifest.root_hash` sourced from `TransactionInfo::ensure_state_checkpoint_hash()` [6](#0-5) ) and to gate state-sync's chunk executor result as "verified." If the state root that a node computes ever diverges from the canonical, ledger-info-signed root — whether from a bug or a hard-fork-only divergence — the primary automated safety net (`ensure_match_transaction_info`) will not catch it, silently reporting a clean replay/verification. This can mask committed-state corruption that differs from the correct VM result, directly matching the "committed state that differs from the correct VM result" and "hard-fork-only divergence during commit, replay, restore" impact categories.

### Likelihood Explanation
This is not an attacker-triggerable exploit against consensus today (write-set and event hashes are still checked, which catches most divergence), but it's a real, unprivileged gap in a critical verification path: any bug affecting only the state/hot-state checkpoint computation (not the write set materialization itself) would pass undetected through chunk-executor verification and `replay_on_archive`, both of which are relied upon as the ground-truth safety check for state-sync and backup/restore correctness. The code's own inline comment confirms this is a known, currently-unmitigated gap tied to upcoming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` work.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `self`-derived (or externally supplied) state checkpoint hash, hot-state checkpoint hash, and `position_state_checkpoint_hash` against the corresponding fields on `txn_info` whenever they are `Some` on the `TransactionInfo`, so that chunk-execution verification and replay-verify tooling cannot report success when the authenticated state root has diverged.

### Proof of Concept
Not directly exploitable as a standalone PoC without a companion bug in checkpoint-hash computation — the gap is a missing check, not a computational error by itself. The affected assertion path can be demonstrated by constructing a `TransactionOutput` whose write-set/events hash correctly but whose corresponding `state_checkpoint_hash`/`position_state_checkpoint_hash` (computed separately by `DoStateCheckpoint`, see [7](#0-6) ) differs from the canonical `TransactionInfo`; calling `ensure_match_transaction_info` on it returns `Ok(())` despite the state-root mismatch, which is confirmed by reading the function body directly [1](#0-0) .

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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
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
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```

**File:** storage/backup/backup-cli/src/backup_types/state_snapshot/restore.rs (L127-136)
```rust
        txn_info_with_proof.verify(li.ledger_info(), manifest.version)?;
        let state_root_hash = txn_info_with_proof
            .transaction_info()
            .ensure_state_checkpoint_hash()?;
        ensure!(
            state_root_hash == manifest.root_hash,
            "Root hash mismatch with that in proof. root hash: {}, expected: {}",
            manifest.root_hash,
            state_root_hash,
        );
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-109)
```rust
                let state_checkpoint_hash = state_checkpoint_hashes[i];
                let event_hashes = txn_output
                    .events()
                    .iter()
                    .map(CryptoHash::hash)
                    .collect::<Vec<_>>();
                let event_root_hash =
                    InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash();
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
                let status = txn_output
                    .status()
                    .as_kept_status()
                    .expect("Already sorted.");
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
```
