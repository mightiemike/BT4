### Title
`replay_on_archive` / `TransactionOutput::ensure_match_transaction_info` silently ignores the position-state (and hot-state) checkpoint hash, allowing a corrupted `ProvablePositionStateSummary`/`position_state_checkpoint_hash` to pass replay verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`) to confirm that a locally re-executed transaction produces the same authenticated result as the `TransactionInfo` committed to the ledger/accumulator. The function explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — fields that, per `execution/executor/src/workflow/do_state_checkpoint.rs`, bind the transaction to the sparse-Merkle/JMT state root and (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is active) the native "trading" position-state root.

### Finding Description
`ensure_match_transaction_info` compares transaction status, gas used, write-set hash (`state_change_hash`), and event root hash against the given `TransactionInfo`, but the function body contains an explicit TODO acknowledging the gap and returns `Ok(())` without ever checking `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`: [1](#0-0) 

These checkpoint hashes are computed in `DoStateCheckpoint::run` from the accumulated Jellyfish-Merkle / native-position sparse-Merkle state (`state_summary`, `position_state_summary`), i.e. they are the authenticated commitment to the *entire resulting world state and native-position ledger*, not just this one transaction's write set: [2](#0-1) [3](#0-2) 

`storage/db-tool/src/replay_on_archive.rs::execute_and_verify` is the only call site exercised for archive/back-up verification; it calls `ensure_match_transaction_info` per-transaction and treats a return of `Ok(())` as "matches": [4](#0-3) 

Because `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are never checked, this per-transaction comparison can never catch a state root divergence: it only compares the write set and events of the individual transaction, not the accumulated Merkle/JMT root that is supposed to be the authenticated commitment of the whole state after that transaction. If the accumulated state root diverges (e.g. due to a storage schema bug, replay-path reinterpretation bug, or bug in `LedgerStateSummary::update`/native position SMT logic elsewhere in the codebase), `replay_on_archive` and other tools relying on `ensure_match_transaction_info` will report a clean pass even though the durable state root committed on-chain no longer matches what local re-execution actually produces.

### Impact Explanation
This breaks the "proof-integrity" invariant required by the task: an authenticated state commitment (accumulator/state-checkpoint root, including the native trading-position root) must be provably bound to correct execution, and any divergence must be detectable at commit/replay time. Here, the replay-verification tool — the mechanism relied upon to catch exactly this class of divergence (hard-fork-only or storage-bug-induced state corruption) — is blind to it for the state-checkpoint and position-checkpoint hash fields. In an environment where `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is active, a bug elsewhere that corrupts the position ledger state, or any bug that corrupts the JMT/hot-state root during storage commit or restore, would go undetected by replay/verify tooling that operators and auditors rely on to catch mainnet ledger corruption.

### Likelihood Explanation
The gap is deterministic and always present — it does not depend on an attacker; it's a structural omission in the verification logic, self-documented by the author's own TODO comment. The likelihood of exploitation/manifestation is tied to any other independent bug that corrupts `state_checkpoint_hash`/`position_state_checkpoint_hash` during commit/restore; the finding here is specifically that verification tooling will not catch such corruption, defeating the purpose of `replay_on_archive` for these fields. Since `position_state_checkpoint_hash` is gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (not yet enabled based on repository state), current mainnet risk is limited to the already-shipped `state_checkpoint_hash`/`hot_state_checkpoint_hash` gap, whose write set/event-hash checks alone are enough to catch *transaction-output* divergence but not root-level storage/restore divergence.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever they are present on both sides (accepting `None` vs `None` only when checkpoints legitimately are not produced for that transaction), consistent with the comment's own suggestion, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet.

### Proof of Concept
Not exploitable via a standalone transaction PoC; this is a verification-logic gap. To demonstrate: run `replay_on_archive` (or the `TransactionReplayer`/chunk-executor verify path that calls `ensure_match_transaction_info`) against a backup where the persisted `TransactionInfo.state_checkpoint_hash` (or `position_state_checkpoint_hash`) has been corrupted/mismatched relative to actual accumulated state, while `write_set`/`events` for the individual transaction are unchanged — the check at [5](#0-4)  will still return `Ok(())`, i.e. replay-verify reports success despite the state root mismatch.

### Citations

**File:** types/src/transaction/mod.rs (L2168-2204)
```rust
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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-49)
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
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-84)
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

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-406)
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
        }
```
