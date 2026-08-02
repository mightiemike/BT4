### Title
`ensure_match_transaction_info` skips state-checkpoint hash verification, letting replay-verify silently accept a diverged state root - (`types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the authoritative comparator used by chunk replay verification (state-sync `verify_execution`) and the `replay_on_archive` backup-verification tool to confirm that a locally re-executed `TransactionOutput` matches an authenticated `TransactionInfo` pulled from a trusted proof/backup. It checks status, gas, write-set hash, and event root hash, but it never checks `state_checkpoint_hash` (or `hot_state_checkpoint_hash`), which is the field that actually attests the correctness of the Sparse/Jellyfish Merkle state-tree root produced by applying the write sets. This is explicitly acknowledged by an in-code TODO. As a result, a bug in state-tree construction (`DoStateCheckpoint`) that produces a wrong state root — while leaving the write set itself byte-identical — is invisible to this verification path.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash = CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

but the function ends with an explicit comment:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
This means `state_checkpoint_hash` and `hot_state_checkpoint_hash` are never compared — not only the not-yet-enabled `position_state_checkpoint_hash`. These fields are populated by `DoLedgerUpdate::assemble_transaction_infos` from `state_checkpoint_output.state_checkpoint_hashes` / `hot_state_checkpoint_hashes`, which in turn come from `DoStateCheckpoint::run` recomputing the Sparse Merkle / Jellyfish Merkle root over the accumulated write sets, separate from hashing the write set bytes themselves. See: [2](#0-1) [3](#0-2) 

`ensure_match_transaction_info` is the sole comparator used by:
1. Chunk-executor replay verification (`verify_execution`, invoked during state-sync/backup transaction replay with `VerifyExecutionMode`): [4](#0-3) 
2. The `db-tool replay-on-archive` verification CLI: [5](#0-4) 
3. `aptos-debugger` and `aptos-move/cli` replay/verify commands (same call site pattern).

Because `write_set_hash` only proves the write set's *contents* are unchanged, and `state_checkpoint_hash` is the field that authenticates that applying those write sets on top of the prior state actually produces the claimed Merkle root, a divergence purely in state-tree construction (e.g., a bug in `DoStateCheckpoint`/`StateStore` batch-update logic, incorrect hot-state materialization, or a state-tree corruption introduced during restore/replay) is not detected by any of these tools. The check that *is* performed (`TransactionOutputListWithProof::verify`, used for API-served proofs) also omits `state_checkpoint_hash` verification — see [6](#0-5) , confirming the gap is systemic to this comparator family, not a one-off oversight.

### Impact Explanation
Replay-verify and backup-verification are the safety nets specifically designed to catch state-commitment divergence (the "hard fork" class of bug: local recomputation differs from the authenticated ledger). By omitting `state_checkpoint_hash`/`hot_state_checkpoint_hash` from the pass/fail decision, these tools can report `PASS`/success on a replay whose state Merkle root is provably wrong, masking exactly the class of bug that the State-Integrity Gate cares about (wrong accumulator/Merkle root accepted as valid, or hard-fork-only divergence during replay/restore not surfaced). Operators, auditors, and automated CI (`replay-verify`) relying on this tool for confidence that archived/restored ledger data matches consensus-committed state get a false positive, allowing corrupted state to be silently accepted as verified.

### Likelihood Explanation
This requires a separate, pre-existing state-tree-construction divergence (write set correct, resulting Merkle root wrong) to actually manifest — this comparator gap does not itself corrupt state, it just fails to detect corruption that originates elsewhere (e.g., a hot-state/`DoStateCheckpoint` bug, a state-restore bug, or shard/parallel batch-update non-determinism). Given the codebase's own TODO flags this as a known, un-mitigated hole intended to be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," and that the state_checkpoint_hash/hot_state_checkpoint_hash gap exists unconditionally today (not gated by that feature flag), the detection gap is live now for any state-checkpoint-hash bug already present or introduced in the future.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived checkpoint hashes (when the transaction has a checkpoint) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and — once trading-native ships — `position_state_checkpoint_hash()`, failing with the same detailed `ensure!` diagnostics as the existing checks. Since the comparator's signature does not currently receive the locally computed state-checkpoint hash, thread it through from `verify_execution` / `replay_on_archive::execute_and_verify` (both already have access to `StateCheckpointOutput`/execution results) so a mismatch is a hard error rather than silently ignored.

### Proof of Concept
1. Introduce (or trigger) a divergence purely in state-tree construction, e.g. a bug in `DoStateCheckpoint::run`/`StateStore` batch update that computes a different `last_checkpoint.root_hash()` for a given set of write sets than the canonical implementation, without changing the write sets themselves.
2. Run `db-tool replay-on-archive` (or the chunk-executor `verify_execution` path during state-sync) against a backup/chunk whose `TransactionInfo.state_checkpoint_hash` reflects the correct root.
3. Observe that `ensure_match_transaction_info` at [7](#0-6)  only compares `write_set_hash` (unaffected by the bug) and returns `Ok(())` at line 2203 without ever inspecting `txn_info.state_checkpoint_hash()`, so `execute_and_verify` in [8](#0-7)  reports no error even though the locally reconstructed state Merkle root differs from the archived/authenticated one.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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
```

**File:** types/src/transaction/mod.rs (L2970-3015)
```rust
        // Verify the events, write set, status, gas used and transaction hashes.
        self.transactions_and_outputs.par_iter().zip_eq(self.proof.transaction_infos.par_iter())
        .map(|((txn, txn_output), txn_info)| {
            // Check the events against the expected events root hash
            verify_events_against_root_hash(&txn_output.events, txn_info)?;

            // Verify the write set matches for both the transaction info and output
            let write_set_hash = CryptoHash::hash(&txn_output.write_set);
            ensure!(
                txn_info.state_change_hash() == write_set_hash,
                "The write set in transaction output does not match the transaction info \
                     in proof. Hash of write set in transaction output: {}. Write set hash in txn_info: {}.",
                write_set_hash,
                txn_info.state_change_hash(),
            );

            // Verify the gas matches for both the transaction info and output
            ensure!(
                txn_output.gas_used() == txn_info.gas_used(),
                "The gas used in transaction output does not match the transaction info \
                     in proof. Gas used in transaction output: {}. Gas used in txn_info: {}.",
                txn_output.gas_used(),
                txn_info.gas_used(),
            );

            // Verify the execution status matches for both the transaction info and output.
            ensure!(
                *txn_output.status() == TransactionStatus::Keep(txn_info.status().clone()),
                "The execution status of transaction output does not match the transaction \
                     info in proof. Status in transaction output: {:?}. Status in txn_info: {:?}.",
                txn_output.status(),
                txn_info.status(),
            );

            // Verify the transaction hashes match those of the transaction infos
            let txn_hash = txn.committed_hash();
            ensure!(
                txn_hash == txn_info.transaction_hash(),
                "The transaction hash does not match the hash in transaction info. \
                     Transaction hash: {:x}. Transaction hash in txn_info: {:x}.",
                txn_hash,
                txn_info.transaction_hash(),
            );
            Ok(())
        })
        .collect::<Result<Vec<_>>>()?;
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-121)
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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L36-60)
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
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;
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
