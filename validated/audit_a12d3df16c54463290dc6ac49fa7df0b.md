## Title
Replay/chunk-verification skips checkpoint-hash comparison, allowing a divergent state root to be silently committed - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the function used to check that a freshly (re-)executed `TransactionOutput` matches an already-authenticated `TransactionInfo` before that output is accepted for commit during chunk replay / state-sync execute-and-verify / replay-verify tooling. It checks status, gas, write-set hash, and event root hash, but explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the locally recomputed values. This mirrors the Hats Protocol pattern of a "checkAfterExecution" gate that validates several invariants but omits the one (owner/signer count) that actually protects the safe from being bricked — here the omitted invariant is the state root itself.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0) . It validates status, gas, write-set hash, and event root hash, but its own trailing comment admits the gap: [2](#0-1) 

This function is the sole gate used in the chunk-executor's execution-verification path: [3](#0-2) 

`verify_execution` is invoked by `remove_and_replay_epoch` whenever `verify_execution_mode.should_verify()` is true [4](#0-3) , which drives full-node chunk replay/verification and archival replay tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) that all call the same `ensure_match_transaction_info`.

Because the `TransactionInfo` being compared against is already cryptographically bound to the ledger (proved by the accumulator/ledger-info signatures elsewhere), this check is not meant to catch a malicious peer forging data — it is the specific mechanism meant to catch a local re-execution that silently diverges from the authentic historical state (e.g., a non-determinism or state-computation regression). Skipping the checkpoint-hash fields means that exact class of divergence — a wrong global state (JMT) root, a wrong hot-state root, or a wrong "native position" state root — passes verification undetected, and `remove_and_apply`/`enqueue_chunk` will then commit the locally-computed (diverged) write sets to durable storage as if they matched the authentic ledger [5](#0-4) .

### Impact Explanation
This falls squarely in the in-scope "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Committed state that differs from the correct VM result or corrupts durable ledger data" categories. A node relying on chunk-executor verify mode, `replay_on_archive`, or the debugger's replay-verify facility can complete a "successful" verified replay while its locally committed state-checkpoint root (or hot-state/position-state root) has actually diverged from the authenticated chain state. This directly undermines the purpose of the verification tooling used to detect state-computation bugs before they propagate (e.g., pre-hard-fork correctness checks, disaster-recovery verification, or full-node execute-and-verify sync), and could let a corrupted/incorrect ledger state be persisted and treated as validated.

### Likelihood Explanation
The gap is unconditionally present in the code path (no feature flag guards the check itself — the comment only discusses gating a different, unrelated new feature `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, but the state_checkpoint_hash omission it flags already exists for the base `state_checkpoint_hash` field used since `TransactionInfo::V0`). Any scenario that produces a legitimate state-computation discrepancy (VM/runtime bug, storage-schema bug, hardware/nondeterminism issue) during chunk replay, verify-execution mode, or `replay_on_archive` will be missed by this specific safety net every time it is exercised, since the comparator is deterministic and always skips these fields.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/expected) against the values produced by local re-execution, failing verification (as the other four checks already do) on any mismatch, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or related trading-native state root features are enabled.

### Proof of Concept
Not independently exploitable via an external attacker (the `TransactionInfo` fields are already accumulator/ledger-info-bound), but demonstrable as a logic gap:
1. Construct two `TransactionOutput`s with identical status/gas/write-set/events but differing only in the state produced by different global state pre-images (so the resulting `state_checkpoint_hash` a correct implementation would compute differs from the `txn_info.state_checkpoint_hash()` supplied).
2. Call `ensure_match_transaction_info(version, txn_info, ...)` — it returns `Ok(())` because none of its `ensure!` checks reference `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`, as seen at [6](#0-5) .
3. In `chunk_executor::verify_execution`, this `Ok(())` allows `remove_and_apply` to proceed and commit the diverged output [7](#0-6) , demonstrating that state-root divergence during replay/verify is not caught.

Note: I was unable to fully confirm within available iterations whether the separate `ledger_update_output::ensure_transaction_infos_match` path used by `StateSyncChunkVerifier` (state-sync execute-and-verify) performs a full-struct comparison that would independently catch this gap for that specific caller; this uncertainty applies only to that one code path, not to the `chunk_executor::verify_execution` / replay-verify tooling path documented above, where the gap is directly confirmed by the code and its own comment.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L617-628)
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
```

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
```rust
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

**File:** execution/executor/src/chunk_executor/mod.rs (L710-758)
```rust
    /// Consume `end_version - begin_version` txns from the mutable input arguments
    /// It's guaranteed that there's no known broken versions or epoch endings in the range.
    fn remove_and_apply(
        &self,
        transactions: &mut Vec<Transaction>,
        persisted_aux_info: &mut Vec<PersistedAuxiliaryInfo>,
        transaction_infos: &mut Vec<TransactionInfo>,
        write_sets: &mut Vec<WriteSet>,
        event_vecs: &mut Vec<Vec<ContractEvent>>,
        begin_version: Version,
        end_version: Version,
    ) -> Result<()> {
        let num_txns = (end_version - begin_version) as usize;
        let txn_infos: Vec<_> = transaction_infos.drain(..num_txns).collect();
        let (transactions, persisted_aux_info, transaction_outputs) = multizip((
            transactions.drain(..num_txns),
            persisted_aux_info.drain(..num_txns),
            txn_infos.iter(),
            write_sets.drain(..num_txns),
            event_vecs.drain(..num_txns),
        ))
        .map(|(txn, persisted_aux_info, txn_info, write_set, events)| {
            (
                txn,
                persisted_aux_info,
                TransactionOutput::new(
                    write_set,
                    events,
                    txn_info.gas_used(),
                    TransactionStatus::Keep(txn_info.status().clone()),
                    TransactionAuxiliaryData::default(), // No auxiliary data if transaction is not executed through VM
                ),
            )
        })
        .multiunzip();

        let chunk = ChunkToApply {
            transactions,
            transaction_outputs,
            persisted_aux_info,
            first_version: begin_version,
        };
        let chunk_verifier = Arc::new(ReplayChunkVerifier {
            transaction_infos: txn_infos,
        });
        self.enqueue_chunk(chunk, chunk_verifier, "replay")?;

        Ok(())
    }
```
