### Title
Replay-verify accepts corrupted position-state (trading-native) roots because `TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash comparison - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single integrity check used by replay/verification tooling to confirm that a locally re-executed transaction output matches an authenticated `TransactionInfo` (the leaf committed to the transaction accumulator). It verifies status, gas, write-set hash, and event-root hash, but explicitly does **not** verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` carried in `TransactionInfoV1`, leaving a documented TODO instead of the check.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event-root hash against `event_root_hash`. Immediately before returning `Ok(())` it contains: [2](#0-1) 

This comment states, in the code itself, that the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is the sole authenticity gate used by `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes transactions from an untrusted archive and calls `ensure_match_transaction_info` per transaction to decide pass/fail: [3](#0-2) . It is also invoked from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`.

The `position_state_checkpoint_hash` field exists specifically to bind `TransactionInfoV1` to the "trading-native"/position-state Merkle root, computed in `DoStateCheckpoint::run` only `if execution_output.compute_trading_native_state_roots`: [4](#0-3) , and then threaded into the assembled `TransactionInfo` by `DoLedgerUpdate::run` via `state_checkpoint_output.position_state_checkpoint_hashes`: [5](#0-4) . That `TransactionInfo` (including the position-state hash field) is exactly what gets hashed into the transaction accumulator leaf and is the object `ensure_match_transaction_info` is supposed to authenticate against.

Because the verifier only checks write-set hash and event-root hash, any divergence confined to the position-state/hot-state checkpoint hash — e.g. from a bug in `compute_position_checkpoint`, from a malicious archive that supplies a locally-computed `TransactionInfo` with a tampered `position_state_checkpoint_hash` but correct write-set/event hashes, or from non-determinism in the trading-native path — passes verification silently.

### Impact Explanation
Replay-verify is a proof-and-restore-path integrity tool: its job is to guarantee that state reconstructed from backups/archives, when re-executed, produces the exact same authenticated ledger state as originally committed (bound to the accumulator root). Because the position/hot state checkpoint hashes are excluded from the comparison, a corrupted or manipulated position state root (part of the committed `TransactionInfoV1`, hence part of the transaction-accumulator leaf hash) is accepted as valid by tooling relying on this function. This can mask a genuine divergence between the authenticated ledger and locally computed state for the trading-native/position-state subsystem, which is exactly the "committed state differs from correct VM result" and "authenticated ... proof context" class of issue in scope. It undermines confidence that `replay_on_archive` (and any caller of `ensure_match_transaction_info`) actually detects position-state corruption/hard-fork divergence in that subsystem.

### Likelihood Explanation
This is not a hypothetical: the gap is explicitly self-documented in the code's own TODO comment, confirming the authors are aware the check is incomplete and that it is reachable through the real `replay_on_archive` verification tool with no additional preconditions beyond the trading-native/position-state feature being active. No privileged access is required to trigger the missing-check condition; it is a straightforward omission in an integrity-checking function on a path that is otherwise exercised on every verified transaction.

### Recommendation
Extend `ensure_match_transaction_info` to also recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the corresponding fields of `txn_info` whenever they are present (i.e., mirror the same rigor already applied to `state_change_hash`/`event_root_hash`), before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/trading-native functionality is relied upon in any verification-sensitive deployment.

### Proof of Concept
1. Enable the trading-native/position-state feature (`compute_trading_native_state_roots` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) so that `TransactionInfoV1.position_state_checkpoint_hash` is populated in committed ledger data.
2. Produce (or simulate a corrupted) backup/archive whose stored `TransactionInfo` for a given version has a correct `state_change_hash`/`event_root_hash` but an incorrect `position_state_checkpoint_hash` (e.g., due to a bug in `compute_position_checkpoint` or deliberate tampering of the backup file, since the archive is otherwise untrusted input to `replay_on_archive`).
3. Run `db-tool replay-on-archive` against this archive; `Verifier::execute_and_verify` calls `ensure_match_transaction_info` per transaction: [6](#0-5) .
4. Because the function never compares `position_state_checkpoint_hash`, verification reports success even though the position-state root diverges from the value that must be authenticated against the transaction accumulator — confirming the exact corrupted field (`TransactionInfoV1.position_state_checkpoint_hash`) escapes detection.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-75)
```rust
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
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L35-45)
```rust
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```
