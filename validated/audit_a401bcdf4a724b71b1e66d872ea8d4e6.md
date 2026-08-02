### Title
Replay-verify integrity check silently skips state-checkpoint hash validation, allowing corrupted checkpoint roots to pass as verified - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated invariant that binds a locally-executed `TransactionOutput` to the ledger's persisted, proof-bearing `TransactionInfo` during chunk-executor verification and archive replay-verify. It checks status, gas, write-set hash, and event root hash, but explicitly and admittedly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the exact fields that commit the Sparse-Merkle/Jellyfish state root into the transaction accumulator leaf.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` computes and asserts equality for:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It ends with an explicit TODO acknowledging the gap:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
This means the function never re-derives or compares `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` against anything computed from local re-execution.

This function is the sole per-transaction commitment check used in two integrity-critical call sites:
1. `execution/executor/src/chunk_executor/mod.rs::verify_execution` — re-executes a chunk of transactions during state-sync/backup verification and calls `txn_out.ensure_match_transaction_info(version, txn_info, Some(write_set), Some(events))` per transaction, using it as the pass/fail gate for accepting persisted `TransactionInfo`s as correct. [2](#0-1) 
2. `storage/db-tool/src/replay_on_archive.rs` — the mainnet replay-verify tool that downloads archived transactions/outputs and confirms the persisted `TransactionInfo` (and therefore the accumulator root it feeds) matches local re-execution. [3](#0-2) 

Because `TransactionInfoV1` (and `TransactionInfoV0`) carry `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class fields that are hashed into the leaf used by `InMemoryTransactionAccumulator` (see `assemble_transaction_infos` in `do_ledger_update.rs`, which builds these into the `TransactionInfo` whose hash becomes an accumulator leaf), any of these fields can diverge from what local execution would produce, yet `ensure_match_transaction_info` would still return `Ok(())`. [4](#0-3) [5](#0-4) 

### Impact Explanation
The state-checkpoint hash is the field that binds the Sparse Merkle / Jellyfish Merkle world-state root (and, for the trading-native code path, the position-state root) into the per-transaction leaf that is accumulated into the ledger's transaction accumulator root, which is itself signed inside `LedgerInfo`. If this hash were wrong (due to a bug elsewhere in state-checkpoint computation, storage corruption, or a malicious archive/backup source feeding data to `replay_on_archive`/chunk-executor verify), the verification gate that is supposed to catch such divergence would not detect it, because it never re-derives or compares this hash. That means "committed" state-checkpoint roots could differ from the correct VM result without replay-verify or chunk-executor's `verify_execution` catching it — directly matching the "wrong state proof/accumulator root accepted as valid" and "committed state that differs from correct VM result" impact categories.

The severity is bounded by the fact that this specific field guard is explicitly gated behind an unfinished, currently-not-fully-wired feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`/trading-native position state), and I could not confirm from the available index whether this feature is already active on mainnet (the flag exists in `aptos_features.rs`/`aptosdb_reader.rs`/framework `features.move`, but I was unable to fully trace its enablement status before running out of search budget). Even absent that specific feature, the same comparator gap applies to the ordinary `state_checkpoint_hash` field which is used unconditionally by all V0/V1 transaction infos — this is not solely a trading-native concern; it is a general absence of state-checkpoint-hash validation in the one function responsible for validating that replayed/re-executed output matches the authenticated ledger commitment.

### Likelihood Explanation
This is not a hypothetical exploit path requiring a privileged actor: it's a structural gap in the verification logic itself, present in code that ships and runs (chunk executor verify-execution and the `replay-on-archive`/replay-verify CLI tools used for mainnet archive integrity checks). The gap is acknowledged by an in-repo TODO, confirming the developers are aware the checkpoint-hash validation path is incomplete, but the comparator is unconditionally missing the check today for the general `state_checkpoint_hash` field on every call, not merely once the trading-native feature ships.

### Recommendation
Extend `ensure_match_transaction_info` to independently recompute the expected `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) from the locally re-executed state and assert equality against `txn_info.state_checkpoint_hash()` (and companions), mirroring the existing pattern used for `write_set_hash` and `event_root_hash`, before any reliance is placed on `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or general replay-verify integrity guarantees.

### Proof of Concept
Not independently reproducible from the available index — the callers (`verify_execution` in `execution/executor/src/chunk_executor/mod.rs`, and `Verifier::verify`/`execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`) both rely exclusively on `ensure_match_transaction_info` as the pass/fail signal per transaction, and that function's own code (with its own TODO) demonstrates the checkpoint-hash fields are excluded from comparison. I was not able to fully confirm within the given tool budget whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/trading-native position-state paths are already live in mainnet configuration (this would need further tracing through `aptosdb_reader.rs`/`aptosdb_internal.rs` and the feature-flag activation state on-chain), so the precise blast radius (trading-native-only vs. general state_checkpoint_hash) is not fully verified and should be confirmed with full repository access before treating this as more than a Medium/High-severity gap in defense-in-depth verification tooling.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
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

**File:** storage/db-tool/src/replay_on_archive.rs (L242-313)
```rust
    // Execute the verify one valid range
    pub fn verify(&self, start: Version, limit: u64) -> Result<Vec<Error>> {
        let mut total_failed_txns = Vec::with_capacity(limit as usize);
        let txn_iter = self
            .backup_handler
            .get_transaction_iter(start, limit as usize)?;
        let mut cur_txns = Vec::with_capacity(limit as usize);
        let mut cur_persisted_aux_info = Vec::with_capacity(limit as usize);
        let mut expected_events = Vec::with_capacity(limit as usize);
        let mut expected_writesets = Vec::with_capacity(limit as usize);
        let mut expected_txn_infos = Vec::with_capacity(limit as usize);
        let mut chunk_start_version = start;
        let executor = AptosVMBlockExecutor::new();
        for item in txn_iter {
            // timeout check
            if let Some(duration) = self.timeout_secs {
                if self.replay_stat.get_elapsed_secs() >= duration {
                    bail!(
                        "Verify timeout: {}s elapsed. Deadline: {}s. Failed txns count: {}",
                        self.replay_stat.get_elapsed_secs(),
                        duration,
                        total_failed_txns.len(),
                    );
                }
            }

            let (
                input_txn,
                persisted_aux_info,
                expected_txn_info,
                expected_event,
                expected_writeset,
            ) = item?;
            let is_epoch_ending = expected_event.iter().any(ContractEvent::is_new_epoch_event);
            cur_txns.push(input_txn);
            cur_persisted_aux_info.push(persisted_aux_info);
            expected_txn_infos.push(expected_txn_info);
            expected_events.push(expected_event);
            expected_writesets.push(expected_writeset);
            if is_epoch_ending || cur_txns.len() >= self.chunk_size {
                let cnt = cur_txns.len();
                while !cur_txns.is_empty() {
                    // verify results
                    let failed_txn_opt = self.execute_and_verify(
                        &executor,
                        &mut chunk_start_version,
                        &mut cur_txns,
                        &mut cur_persisted_aux_info,
                        &mut expected_txn_infos,
                        &mut expected_events,
                        &mut expected_writesets,
                    )?;
                    // collect failed transactions
                    total_failed_txns.extend(failed_txn_opt);
                }
                self.replay_stat.update_cnt(cnt as u64);
                self.replay_stat.print_tps();
            }
        }
        // verify results
        let fail_txns = self.execute_and_verify(
            &executor,
            &mut chunk_start_version,
            &mut cur_txns,
            &mut cur_persisted_aux_info,
            &mut expected_txn_infos,
            &mut expected_events,
            &mut expected_writesets,
        )?;
        total_failed_txns.extend(fail_txns);
        Ok(total_failed_txns)
    }
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-123)
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
                let txn_info_hash = txn_info.hash();
                (txn_info, txn_info_hash)
```
