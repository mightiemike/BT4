I'll investigate the codebase for genuine local integrity issues analogous to the "missing cap check before minting" pattern—looking at write-set conversion, transaction info construction, storage commit, accumulators, Merkle proofs, restore flows, and authenticated API responses.This looks promising. Let me trace `ensure_match_transaction_info`'s callers and confirm the state checkpoint hash gap is not covered elsewhere in that specific verification path.## Title
`ensure_match_transaction_info` skips comparing `state_checkpoint_hash` (and hot/position checkpoint hashes), letting replay-verify tooling accept a wrong state root as matching - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by both the offline replay-verify tool (`storage/db-tool/src/replay_on_archive.rs`) and the chunk-executor's execution-verification path (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) to confirm that a locally re-executed transaction matches the archived/proven `TransactionInfo`. It checks status, gas used, write-set hash, and event root hash, but never compares `state_checkpoint_hash` (nor `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) against the value re-derived from local execution. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` verifies:
- `status` vs. `txn_info.status()`
- `gas_used` vs. `txn_info.gas_used()`
- `write_set_hash` vs. `txn_info.state_change_hash()`
- `event_root_hash` vs. `txn_info.event_root_hash()`

But it never touches `txn_info.state_checkpoint_hash()` (nor the hot-state/position variants). The comment right above the `Ok(())` explicitly documents this gap: [2](#0-1) 

`state_checkpoint_hash` is the field that binds a checkpoint transaction to the cumulative Jellyfish Merkle state root at that version — it is the strongest authenticated signal that the *entire* accumulated state (not just this transaction's write set) matches. A transaction's `write_set_hash` only proves that the write ops produced by this one transaction match; it says nothing about whether applying the full history of writes onto the state tree from genesis actually produces the expected merkle root. Divergence there (e.g. from a state-application bug, a restore bug, or a schema-corrupting bug elsewhere in the executor/DB) would only surface via `state_checkpoint_hash`.

Both call sites use this function specifically to validate re-executed results against externally-provided, ledger-info-proven `TransactionInfo`s:
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` — the tool whose entire purpose is to detect ledger/state divergence by replaying archived transactions. [3](#0-2) 
- `execution/executor/src/chunk_executor/mod.rs::verify_execution` — used during backup/restore chunk replay-verification. [4](#0-3) 

By contrast, the online chunk-apply path (`ChunkExecutor::update_ledger`) does feed `known_state_checkpoints` derived from the trusted `TransactionInfo`s into `DoStateCheckpoint::run()`, which enforces the checkpoint hash there. [5](#0-4) 
So the state-checkpoint-hash check exists in the live chunk-apply/state-sync path but is absent from the transaction-level comparator that replay-verify and chunk-replay-verify rely on.

### Impact Explanation
This is a proof/commitment-integrity gap in the tooling whose sole job is to catch state divergence: if a bug in the write-set application, restore path, or JMT commit logic corrupts the cumulative state root while individual transaction write-sets/events/gas/status still match (a very plausible failure mode for state-application or restore bugs, as opposed to VM-output bugs), `replay_on_archive`'s verify pass and `ChunkExecutorTrait::verify_execution` would both report success even though the authenticated state root has diverged from the correct value. This directly matches the "hard-fork-only divergence during commit/replay/restore/proof verification" impact category: it is exactly the kind of divergence these audit tools exist to detect, and the missing checkpoint-hash comparison means such divergence is silently accepted as valid.

### Likelihood Explanation
The gap is unconditional (no feature-flag guarding whether `state_checkpoint_hash` gets checked in this function — it's simply omitted for all cases), and is on the hot path of the two tools designed specifically to catch this class of bug. It doesn't require any privileged access to trigger; it only requires that a genuine local state-divergence bug exists elsewhere (e.g. in restore, in commit, or in some future protocol change), at which point this comparator's blind spot means the replay-verify/backup-verify safety net fails to catch it.

### Recommendation
Add explicit comparisons in `ensure_match_transaction_info` between the locally-recomputed `state_checkpoint_hash` (and, where applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) and the corresponding fields on `txn_info`, mirroring the checks already done via `DoStateCheckpoint`'s `known_state_checkpoints` in the live chunk-apply path. Since this function operates transaction-by-transaction and checkpoint hashes are only populated on checkpoint transactions, the comparison should be conditional on `txn_info.has_state_checkpoint_hash()`.

### Proof of Concept
Not directly demonstrable as a state-changing exploit from user input; the bug is structural in the verification logic itself. Concretely: construct (or simulate via a modified test) a `TransactionOutput` whose write-set/events/gas/status match a given `TransactionInfo`, but whose position/JMT-derived checkpoint root differs from `txn_info.state_checkpoint_hash()` (e.g. by feeding a `TransactionInfo` from one execution run and a `TransactionOutput` produced against a state store with an injected root-hash divergence). Calling `ensure_match_transaction_info` on this pair returns `Ok(())`, confirming the missing invariant.

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
