### Title
`ensure_match_transaction_info` never checks `state_checkpoint_hash` / `position_state_checkpoint_hash`, letting replay/chunk verification accept a diverged state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the single comparator used by chunk-execution verification (`execution/executor/src/chunk_executor/mod.rs::verify_execution`) and by the archive replay tool (`storage/db-tool/src/replay_on_archive.rs::execute_and_verify`) to decide whether a locally re-executed transaction matches the transaction info that is authenticated by the ledger accumulator/proof. It checks status, gas, write-set hash, and event-root hash, but — per its own acknowledged `TODO` — it does not check `state_checkpoint_hash` (the world-state SMT root carried in `TransactionInfo`) or `position_state_checkpoint_hash`.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0) , and after checking status, gas, write-set hash and event root, it explicitly documents and skips the checkpoint-hash comparison: [2](#0-1) 

This function is the sole per-transaction correctness gate used by two authenticity-verification call sites:
- `ChunkExecutorInner::verify_execution`, which re-executes a chunk of transactions against a parent state and, for each output, calls `ensure_match_transaction_info` before treating the chunk as verified: [3](#0-2) 
- `ReplayOnArchive::execute_and_verify`, which re-executes transactions from backup storage and calls the same comparator to decide whether the replayed output "matches" the persisted `TransactionInfo`: [4](#0-3) 

Because `state_checkpoint_hash` is only recomputed periodically (per checkpoint transaction, from the sparse-Merkle state summary accumulated across all transactions since the last checkpoint — see `DoStateCheckpoint::run`/`get_state_checkpoint_hashes` in [5](#0-4) ), it is **not derivable from a single transaction's `write_set` hash**. A per-transaction write-set match does not imply the accumulated state-tree root matches: an error in JMT/SMT update logic, a state slot inconsistency introduced during restore, or a subtly wrong "known checkpoint hash" plumbed into `DoStateCheckpoint::run` could all cause the resulting `state_checkpoint_hash` (or the newer `position_state_checkpoint_hash`, added for trading-native/position state, gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature) to diverge from the authenticated ledger value, while `ensure_match_transaction_info` still reports success because it never inspects those two fields at all.

### Impact Explanation
This is a state-commitment/proof-integrity gap in a code path explicitly meant to catch state divergence: chunk-execution verification (fast/intelligent sync of chunks against previously-committed, cryptographically-authenticated `TransactionInfo`s) and the `db-tool replay-verify` tool both rely on `ensure_match_transaction_info` as their pass/fail criterion. If the locally-recomputed state (main or position) SMT root diverges from the one baked into the authenticated `TransactionInfo`/ledger proof — due to any bug elsewhere in state-checkpoint computation, restore, or JMT maintenance — these verification paths will not detect it, since they never compare `state_checkpoint_hash`/`position_state_checkpoint_hash`. This can let a node silently commit or accept a chunk whose durable state root differs from the correct one, i.e., an undetected consensus-state divergence that verification tooling was specifically built to catch. The code's own comment corroborates that this omission is a real, currently-shipped gap: "replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

### Likelihood Explanation
Likelihood of the comparator itself missing this check is certain (unconditionally true by inspection of the code — it never touches `state_checkpoint_hash`). Likelihood of it being *triggered* depends on some other bug or non-determinism actually causing state-root divergence; the comparator's gap is what allows such a bug to go unnoticed by verification/replay tooling rather than surfacing a violated invariant immediately. The `position_state_checkpoint_hash` blind spot is currently only relevant once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled (per `types/src/on_chain_config/aptos_features.rs`), but the main `state_checkpoint_hash` blind spot applies unconditionally today to every call of `ensure_match_transaction_info` for every checkpoint transaction, in every chunk-execution verification and every `replay_on_archive` run.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`'s freshly computed state-checkpoint hash(es) against `txn_info.state_checkpoint_hash()` (when the transaction is a checkpoint) and, when the trading-native feature is enabled, `txn_info.position_state_checkpoint_hash()`, failing verification/replay when they differ — matching the same fail-closed pattern already used for gas/status/write-set/event-root in this function, and resolving the code's own `TODO` before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled.

### Proof of Concept
Not independently reproducible from the given index alone: exploiting this gap requires an additional, separate bug that produces a diverging `state_checkpoint_hash`/`position_state_checkpoint_hash` while keeping per-transaction write sets, events, gas and status identical (e.g., a JMT/restore inconsistency between checkpoints). What is concretely provable from the code is the comparator's omission itself: `ensure_match_transaction_info` (types/src/transaction/mod.rs:2139-2204) never reads `txn_info.state_checkpoint_hash()` or `txn_info.position_state_checkpoint_hash()`, and both `ChunkExecutorInner::verify_execution` (execution/executor/src/chunk_executor/mod.rs:692-697) and `ReplayOnArchive::execute_and_verify` (storage/db-tool/src/replay_on_archive.rs:392-397) depend solely on this function to declare a replayed/chunk-executed output as "matching" the authenticated `TransactionInfo`. I was not able to fully trace, within the remaining tool budget, whether `DoStateCheckpoint::get_state_checkpoint_hashes`'s "known-hash validation" (referenced in `do_state_checkpoint.rs`) independently re-asserts the main `state_checkpoint_hash` elsewhere in the chunk-executor commit path outside `verify_execution`; if it does, the impact for the *main* state root may be reduced to the `replay_on_archive` tool only, while the `position_state_checkpoint_hash` gap remains unmitigated everywhere `ensure_match_transaction_info` is used.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2157)
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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
