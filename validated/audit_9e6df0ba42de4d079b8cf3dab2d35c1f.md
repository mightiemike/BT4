### Title
`TransactionOutput::ensure_match_transaction_info` never validates `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, letting chunk-replay verification and archive-replay tooling accept a corrupted world-state root as valid - ([File: types/src/transaction/mod.rs])

### Summary
`ensure_match_transaction_info`, the function used by chunk-executor replay verification (`verify_execution`) and by `db-tool`'s `replay_on_archive` / CLI replay tooling to confirm that locally-recomputed execution results match the authenticated `TransactionInfo` stored on-chain, checks status, gas, the write-set hash (`state_change_hash`) and the event root hash, but explicitly and admittedly skips comparing the state (and hot-state/position-state) checkpoint hash fields of `TransactionInfo`.

### Finding Description
`TransactionOutput::ensure_match_transaction_info` at [1](#0-0)  validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- recomputed event root hash vs `txn_info.event_root_hash()`

but it contains no comparison at all against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`. The function's own comment documents this gap: [2](#0-1) 

This is the sole comparator used by:
- `ChunkExecutor::verify_execution`, which re-executes a chunk of transactions locally and calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` to decide whether local execution matches the authenticated `TransactionInfo` pulled from storage/backup during chunk replay: [3](#0-2) 
- `storage/db-tool/src/replay_on_archive.rs` and `aptos-move/cli/src/commands.rs` (transaction replay/debugging tooling), both of which call the same function to validate replayed execution against the historical `txn_info`: [4](#0-3) 

The `state_checkpoint_hash` is the root hash of the Sparse/Jellyfish Merkle Tree describing the entire world state at that version — it is the single most important commitment for state integrity, more so even than the per-transaction `state_change_hash` (which only covers that transaction's own write set, not the cumulative ledger state). Because `ensure_match_transaction_info` never checks it, any of the following can slip through chunk-replay verification or archive replay tooling undetected:
- A locally computed state root that diverges from the authenticated on-chain state root (e.g. due to a bug in `DoStateCheckpoint`/JMT update logic, a bad snapshot restore, or non-determinism between execution and checkpoint code paths) will not be flagged by `verify_execution`/`replay_on_archive`, since these tools only check write-set and event hashes, not the state checkpoint root.
- This directly undermines the intended purpose of "replay-verify" as a state-integrity safety net: an operator or CI job relying on `replay_on_archive`/chunk-executor verify mode to catch state divergence bugs will get a false "success" even when the authenticated position/state root has silently diverged from local execution.

### Impact Explanation
If the underlying execution/checkpoint pipeline (e.g., `DoStateCheckpoint`, JMT update, or a future "trading-native"/position-state feature under active development per the TODO) produces a wrong state root, this verification path is the last line of defense that is supposed to catch it during chunk replay or archival replay-verify. Because the check is missing, a state-root divergence bug can go undetected by the verification tooling, silently corrupting the durable ledger's provable state view while all automated integrity checks report success. This matches the "Committed state that differs from the correct VM result" and "authenticated API/state-view output bound to the wrong version/root" impact categories in the state-integrity gate.

### Likelihood Explanation
Likelihood of the underlying trigger (a real state-root-computation divergence) is Low on its own — it requires a separate bug or non-determinism in the checkpoint/state-commit code path. However, given the comment indicates this is being surfaced deliberately ahead of enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, and the fact that the check is *unconditionally* skipped for all replay/chunk-verify flows today (not gated behind that feature), the exposure window is present now, and the failure mode ("verification tool reports success while state has silently diverged") is exactly the kind of high-impact/low-detectability scenario the gate is meant to catch.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally computed/derived state checkpoint hash(es) against `txn_info.state_checkpoint_hash()` (and, where applicable, `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`) whenever `TransactionOutput`/caller has that data available (e.g., pass in the computed checkpoint hash from `DoStateCheckpoint` similarly to how `write_set`/`events` are passed), and fail verification/replay if they mismatch, rather than deferring this to a future TODO.

### Proof of Concept
Not independently reproducible as a standalone PoC without also demonstrating a state-root-divergence bug in the checkpoint pipeline (none was found in this scan); the finding here is the *verification gap itself*: inspection of [1](#0-0)  confirms no assertion exists comparing any checkpoint-hash field, and both call sites ( [5](#0-4) , [4](#0-3) ) rely exclusively on this function to declare replay verification "matched." I was not able to fully trace `storage/db-tool/src/replay_on_archive.rs`'s exact call site or confirm whether it independently re-checks the state root elsewhere in that file (only one grep match was found and I did not have remaining iterations to read its surrounding context) — this should be independently verified before treating this as a fully confirmed high-severity finding.

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

**File:** aptos-move/cli/src/commands.rs (L2809-2813)
```rust
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
        }
```
