### Title
`TransactionOutput::ensure_match_transaction_info` skips checkpoint-hash validation, letting replay/verify accept a wrong committed state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by both replay-verification tooling and the chunk-executor's verify-execution path to confirm that a locally re-executed transaction matches the authenticated `TransactionInfo` recorded on-chain/in backups. The function explicitly, and admittedly (per its own TODO), omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the very fields that commit the Sparse-Merkle/JMT state root. This means a state-root divergence between local execution and the authenticated ledger data is invisible to these integrity checks.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates only status, gas used, write-set hash, and event-root hash against a supplied `TransactionInfo`. The function's trailing comment makes the gap explicit: [2](#0-1) 

i.e. "this comparator ignores the checkpoint hashes ... so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This comparator is the sole state-integrity gate in the following consumers:
- The `db-tool` replay-verify utility, which re-executes archived transactions and compares them to the authenticated `TransactionInfo` pulled from backups: [3](#0-2) 
- The chunk executor's `verify_execution`, used during backup restore / chunk-sync verification when `VerifyExecutionMode::should_verify()` is set: [4](#0-3) 
- The Move debugger's mismatch printer: [5](#0-4) 

None of these paths independently re-derive and compare the Sparse Merkle state-checkpoint root, hot-state root, or the newer `position_state_checkpoint_hash` field (a "repurposed reserved field" in `TransactionInfoV1`, see [6](#0-5) ) against what local re-execution/state-checkpoint computation actually produces. Only the write-set hash and event-root hash — not the resulting state tree root — are checked. Since the state-checkpoint hash is what ultimately proves the Merkle/JMT state root committed for a version, this is precisely the class of proof/commit-binding check the state-integrity gate calls out ("Accumulators, Jellyfish Merkle structures, versioned state views, and restore paths must preserve deterministic proof binding").

Because `chunk_executor::verify_execution` (used by chunk-based restore/replay-verify flows, see `remove_and_replay_epoch` at [7](#0-6) ) relies exclusively on this comparator, a divergence introduced by a VM/state-computation bug, a hard-fork-only non-determinism, or corrupted/malicious backup archive data that nonetheless carries a self-consistent `write_set`/`events` (matching hashes) but a different resulting state root would pass both the CLI replay tool and the executor's own verify-execution path without any error being raised.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result... must not be silently accepted" and "restore paths must preserve deterministic proof binding" invariants called out in the task's proof/storage pivots. Concretely:
- `storage/db-tool/replay_on_archive` and the `replay-verify` coordinator (used to validate archived chain history and catch state non-determinism / hard-fork divergence bugs before they reach production) can report success even though the state root diverges — defeating the primary purpose of this tool as a safety net for silent state corruption.
- The chunk executor's own execution-verification mode (invoked during backup restore with verification enabled) can similarly accept and commit transaction outputs whose resulting state tree differs from the archived authenticated data, without flagging a mismatch, because it reuses the same comparator.

This is a High severity state-integrity gap: it doesn't fabricate a forged proof by itself, but it disables the mechanism meant to detect wrong/corrupted committed state during restore and replay-verification, which is exactly the "hard-fork-only divergence during commit, replay, restore, or proof verification" category called out as in-scope.

### Likelihood Explanation
This is not a hypothetical: the gap is real, current, unconditional code (not gated behind an unmerged feature) and is openly acknowledged by the author's own TODO comment referencing an unreleased feature flag (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that has not yet been wired in to close it. Any scenario that produces a divergent state checkpoint hash while keeping write-set/events/gas/status consistent — such as a non-deterministic VM bug, a bug in `DoStateCheckpoint`, or a corrupted/tampered backup archive with an internally-consistent `TransactionInfo`+outputs pair (which does not need to be validator-signed at the point `ensure_match_transaction_info` is invoked, since the accumulator/ledger-info signature check happens separately/later and is not required for this specific comparison call sites) — would go undetected by these tools today.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute and compare the state-checkpoint hash (and, when present, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against the values carried in `txn_info`, gated appropriately by whichever feature flags currently produce these hashes (mirroring the logic in `DoLedgerUpdate::assemble_transaction_infos`, see [8](#0-7) ). At minimum, `replay_on_archive` and chunk-executor `verify_execution` should fail loudly (not silently report success) whenever the local state-checkpoint hash cannot be reconciled with the corresponding authenticated field.

### Proof of Concept
1. Take (or construct) a `TransactionOutput` whose `write_set` and `events` hash to the same `state_change_hash`/`event_root_hash` recorded in a given `TransactionInfo`, gas/status also matching, but where the actual resulting Sparse Merkle Tree root (computed by locally applying the write set) differs from `txn_info.state_checkpoint_hash()` (e.g., due to a non-deterministic execution bug or a hand-crafted archive with an inconsistent checkpoint hash).
2. Call `txn_output.ensure_match_transaction_info(version, &txn_info, Some(&write_set), Some(&events))` as done in `storage/db-tool/src/replay_on_archive.rs` line 392, or via `chunk_executor::verify_execution` line 692.
3. Observe that `Ok(())` is returned — no mismatch is reported — despite the state root diverging, because the function never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.

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

**File:** types/src/transaction/mod.rs (L2452-2453)
```rust
    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
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

**File:** execution/executor/src/chunk_executor/mod.rs (L617-631)
```rust
            // Try to run the transactions with the VM
            let next_begin = if verify_execution_mode.should_verify() {
                self.verify_execution(
                    transactions,
                    persisted_aux_info,
                    transaction_infos,
                    write_sets,
                    event_vecs,
                    batch_begin,
                    batch_end,
                    verify_execution_mode,
                )?
            } else {
                batch_end
            };
```

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
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

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L233-246)
```rust
    fn print_mismatches(
        txn_outputs: &[TransactionOutput],
        expected_txn_infos: &[TransactionInfo],
        first_version: Version,
    ) {
        for idx in 0..txn_outputs.len() {
            let txn_output = &txn_outputs[idx];
            let txn_info = &expected_txn_infos[idx];
            let version = first_version + idx as Version;
            txn_output
                .ensure_match_transaction_info(version, txn_info, None, None)
                .unwrap_or_else(|err| println!("{}", err))
        }
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
