## Finding

### Title
`ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, letting replay-verify accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
The bug-class from the external report is "the code checks some fields of an authoritative structure but silently skips validating the field that actually encodes the state produced by the operation, so a divergent/incorrect result can be accepted as matching." The Aptos-native analog is `TransactionOutput::ensure_match_transaction_info`, the sole correctness check used by chunk-executor replay-verification (`ChunkExecutor::verify_execution`), the CLI transaction-replay tool, and the debugger's mismatch printer. It checks `status`, `gas_used`, `write_set` hash, and `event_root_hash`, but never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that actually encode the resulting Jellyfish Merkle / hot-state / position-state root after applying the write set.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and explicitly documents the gap at [2](#0-1) , stating the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)".

This function is invoked from three call sites, none of which supplement it with a separate checkpoint-hash check:
- `ChunkExecutor::verify_execution`, the replay-verification path used by state-sync/backup "verify" modes and `db-tool`'s `replay_on_archive`: [3](#0-2) 
- CLI transaction replay tooling: [4](#0-3) 
- The debugger's mismatch printer: [5](#0-4) 

Crucially, `verify_execution` obtains its `TransactionOutput`s via `DoGetExecutionOutput::by_transaction_execution`, which only re-executes the VM and never runs `DoStateCheckpoint` (the component that actually recomputes and cross-checks the JMT/hot-state/position-state roots against known values, at [6](#0-5) ). So in this replay path, the state-root computation is architecturally never performed or compared — `ensure_match_transaction_info` is the *only* correctness gate, and it skips exactly the field that would catch a state-root divergence.

By contrast, the normal commit/state-sync path is safe: `ChunkExecutor::update_ledger` does run `DoStateCheckpoint` with `known_state_checkpoints` and asserts the recomputed root equals the known one ( [7](#0-6) ), and `StateSyncChunkVerifier::verify_chunk_result` performs full `TransactionInfo` struct equality via `ensure_transaction_infos_match` ( [8](#0-7) ), which does include the checkpoint-hash fields since they're part of the enum variant.

### Impact Explanation
If a bug exists in the JMT merge, hot-state root computation, or position-state root computation (i.e., in the logic that turns a correct write set into the on-chain state root), the write-set hash and event hash can still match while the state root diverges — a classic hard-fork-class divergence. `verify_execution`/replay-verify tooling (used by `db-tool replay-verify`, backup-restore `--verify` transaction restore, and the debugger) would report success even though the locally computed authenticated state root differs from the one embedded in the canonical, consensus-signed `TransactionInfo`. This directly matches the in-scope impact "Wrong accumulator root ... or state proof accepted as valid" / "Hard-fork-only divergence during ... replay ... or proof verification," since it silently defeats the integrity guarantee that replay-verify is supposed to provide.

### Likelihood Explanation
This is not attacker-triggerable in the traditional sense (it requires an underlying state-computation divergence, e.g., a JMT/hot-state/position-state bug, or corrupted/forked chunk data being fed through the verify path) — consistent with "hard-fork-only" class issues. However, given that the code comment already documents the exact same gap for `position_state_checkpoint_hash` (pending `COMPUTE_TRADING_NATIVE_STATE_ROOTS`), the same architectural gap already unconditionally applies to the always-on `state_checkpoint_hash` and `hot_state_checkpoint_hash` fields today, in the `verify_execution` replay path.

### Recommendation
In `ensure_match_transaction_info`, add checks that compare `self`'s recomputed state checkpoint hash(es) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` when applicable, or ensure `ChunkExecutor::verify_execution` runs the full `DoStateCheckpoint`/`DoLedgerUpdate` pipeline (not just raw VM execution) so that state-root recomputation and comparison actually occurs before declaring replay-verification successful.

### Proof of Concept
1. Introduce (hypothetically) a subtle divergence in JMT root computation logic that does not affect the serialized write set (e.g., a bug in `LedgerStateSummary::update`).
2. Run `db-tool replay-verify` (or restore with `--verify`) over a range of transactions whose canonical `TransactionInfo`s carry the correct `state_checkpoint_hash`.
3. `ChunkExecutor::verify_execution` re-executes via `DoGetExecutionOutput::by_transaction_execution` and calls `txn_out.ensure_match_transaction_info(...)` per transaction at [9](#0-8) .
4. Because `ensure_match_transaction_info` never inspects `state_checkpoint_hash`, the check passes despite the local state root being wrong, and the tool reports a clean, successful replay.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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

**File:** aptos-move/cli/src/commands.rs (L2797-2813)
```rust
        // Materialize into transaction output and check if the outputs match.
        let txn_output = vm_output.into_transaction_output().map_err(|err| {
            CliError::UnexpectedError(format!(
                "Failed to materialize into transaction output: {}",
                err
            ))
        })?;

        // When local package overrides are in use the replayed code diverges from
        // what was originally executed on-chain (different instructions, gas, etc.),
        // so output comparison is meaningless and is automatically skipped.
        let skip_comparison = self.skip_comparison || !self.use_local_package.is_empty();
        if !skip_comparison {
            txn_output
                .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L192-234)
```rust
    fn get_state_checkpoint_hashes(
        execution_output: &ExecutionOutput,
        known_state_checkpoints: Option<Vec<Option<HashValue>>>,
        computed_last_checkpoint_hash: HashValue,
        label: &str,
    ) -> Result<Vec<Option<HashValue>>> {
        let _timer = OTHER_TIMERS.timer_with(&[&format!("get_{label}_checkpoint_hashes")]);

        let num_txns = execution_output.to_commit.len();
        let last_checkpoint_index = execution_output
            .to_commit
            .state_update_refs()
            .last_inner_checkpoint_index();

        if let Some(known) = known_state_checkpoints {
            ensure!(
                known.len() == num_txns,
                "Bad number of known {label} hashes. {} vs {}",
                known.len(),
                num_txns,
            );
            if let Some(idx) = last_checkpoint_index {
                ensure!(
                    known[idx] == Some(computed_last_checkpoint_hash),
                    "{label} root hash mismatch with known hashes passed in. {:?} vs {:?}",
                    known[idx],
                    Some(computed_last_checkpoint_hash),
                );
            }
            Ok(known)
        } else {
            if !execution_output.is_block {
                // We should enter this branch only in test.
                execution_output.to_commit.ensure_at_most_one_checkpoint()?;
            }

            let mut out = vec![None; num_txns];
            if let Some(index) = last_checkpoint_index {
                out[index] = Some(computed_last_checkpoint_hash);
            }
            Ok(out)
        }
    }
```

**File:** execution/executor-types/src/ledger_update_output.rs (L92-114)
```rust
    pub fn ensure_transaction_infos_match(
        &self,
        transaction_infos: &[TransactionInfo],
    ) -> Result<()> {
        ensure!(
            self.transaction_infos.len() == transaction_infos.len(),
            "Lengths don't match. {} vs {}",
            self.transaction_infos.len(),
            transaction_infos.len(),
        );

        let mut version = self.first_version();
        for (txn_info, expected_txn_info) in
            zip_eq(self.transaction_infos.iter(), transaction_infos.iter())
        {
            ensure!(
                txn_info == expected_txn_info,
                "Transaction infos don't match. version:{version}, txn_info:{txn_info}, expected_txn_info:{expected_txn_info}",
            );
            version += 1;
        }
        Ok(())
    }
```
