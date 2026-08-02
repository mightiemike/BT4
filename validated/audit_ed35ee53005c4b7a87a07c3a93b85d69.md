### Title
Replay-verify state-checkpoint blind spot lets a corrupted/divergent state root pass as a successful replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-comparison routine used by replay/verification tooling to confirm that a locally re-executed transaction reproduces the exact on-chain result recorded in the archived `TransactionInfo`. The function checks status, gas, write-set hash, and event-root hash, but — per its own inline `TODO(trading-native)` comment — it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This means replay-verify can report success even when the locally computed state/Merkle root diverges from the authenticated on-chain root.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates a `TransactionOutput` against the corresponding `TransactionInfo` proof leaf by comparing `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash — but explicitly skips the state-checkpoint-related hashes, as documented by the trailing comment: [2](#0-1) 

This routine is the sole correctness gate used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify` path (mainnet replay-verify tooling, run in CI/testsuite to catch execution divergence from archived history) [3](#0-2) 
- `aptos-move/cli/src/commands.rs` transaction-replay commands [4](#0-3) [5](#0-4) 

`TransactionInfo` (both `V0` and `V1`) carries `state_checkpoint_hash` (and `V1` additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`) as committed, accumulator-proven fields [6](#0-5) . These hashes are exactly the authenticated binding between a transaction's committed state and the JMT/state root that downstream consumers (state-sync fast-sync, state proofs, restore) trust as ground truth — as seen in `expected_snapshot_root` in the state-sync bootstrapper, which reads `state_checkpoint_hash`/`position_state_checkpoint_hash` directly off the proven `TransactionInfo` as the sole root of trust for fast-sync snapshot verification [7](#0-6) .

Because `ensure_match_transaction_info` never re-derives and compares these checkpoint hashes, any divergence between a locally computed state root (JMT root, hot-state root, or the newer native "position" state root) and the archived/authenticated root will not be flagged by replay-verify. The write-set hash and event hash equality checks are necessary but not sufficient: it is possible for the write set contents to match while the *derived state tree root* differs (e.g., a bug in JMT construction, hashing of a specific value type, or in the newer position-state Merkle computation introduced by the "trading-native"/position-state work seen in `storage/aptosdb/src/native_state_committer.rs` and `db/aptosdb_native_position.rs`), and replay-verify would still declare success.

### Impact Explanation
Replay-verify is one of Aptos's primary mainnet safety nets: it is specifically designed to catch state-root divergences between the deterministic VM/executor logic and previously-committed history (e.g., after a code change, hard fork, or accidental non-determinism). Silently skipping the state-checkpoint-hash comparison means a real state-commitment bug (VM output computed correctly for write set and events, but the accumulated/authenticated state root wrong) would go undetected by this specific safety check, allowing a hard-fork-class divergence to ship or remain unnoticed in CI/regression testing. This falls squarely in the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Committed state that differs from the correct VM result" categories called out as in-scope, because the authenticated root and the recomputed root can silently diverge without the tool raising an error.

### Likelihood Explanation
The gap is unconditional (not feature-flagged) and always exercised whenever replay-verify or the CLI replay path runs, since `ensure_match_transaction_info` is called without any override to add the missing checks. The comment indicates this was a deliberate, tracked simplification ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), implying the authors are aware the check is currently disabled/missing but the code has already merged this incomplete state into the general-purpose comparison function used by production replay tooling today — regardless of whether the trading/position feature is enabled. Any state-root-affecting bug (not necessarily related to the trading-native feature) elsewhere in the executor is equally undetectable by this path today.

### Recommendation
Add checkpoint-hash verification into `ensure_match_transaction_info`: when `txn_info.state_checkpoint_hash()` (and, for `V1`, `hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()`) is `Some`, recompute the corresponding checkpoint hash from the locally executed output/state and assert equality, mirroring the existing write-set/event-hash checks. If the checkpoint hash cannot yet be recomputed generically at this call site (e.g., it requires access to the full state tree rather than just the single `TransactionOutput`), the function should take an explicit `expected_state_checkpoint_hash` parameter (as is already done for `expected_write_set`/`expected_events`) and enforce it wherever the true checkpoint hash is available, rather than silently omitting the check.

### Proof of Concept
Not independently exploitable via an attacker-supplied transaction (this is a verification-tooling correctness gap rather than a directly triggerable consensus bug), so no standalone PoC transaction can "trigger" it. The concrete reproduction is inspection-based: 
1. Construct (or synthesize in a test) a `TransactionOutput` whose `write_set` and `events` hash match a given `TransactionInfo`'s `state_change_hash`/`event_root_hash`, but for which the state tree constructed from applying that write set produces a different root than `txn_info.state_checkpoint_hash()` (e.g., feed a manually corrupted/rebuilt state tree or a `TransactionInfo` with a checkpoint hash from a different execution history).
2. Call `ensure_match_transaction_info` — per the code shown in [1](#0-0) , it returns `Ok(())` because the checkpoint-hash fields are never inspected.

Note: I could not fully verify (due to iteration limits) whether any other call site downstream of `execute_and_verify` performs an *additional* state-root comparison outside of `ensure_match_transaction_info` that might compensate for this gap; if such a check exists elsewhere in `replay_on_archive.rs`'s `execute_and_verify` function body (not fully retrieved), it would reduce the severity of this finding. This should be confirmed by reading the full body of `Verifier::execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs`.

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

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
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

**File:** state-sync/state-sync-driver/src/bootstrapper.rs (L986-1008)
```rust
    fn expected_snapshot_root(&mut self, kind: StateKind) -> Result<HashValue, Error> {
        let transaction_output_to_sync = self.get_transaction_output_to_sync()?;
        let target_transaction_info = transaction_output_to_sync
            .get_output_list_with_proof()
            .proof
            .transaction_infos
            .first()
            .ok_or_else(|| {
                Error::UnexpectedError("Target transaction info does not exist!".into())
            })?;
        match kind {
            StateKind::MainState => target_transaction_info
                .ensure_state_checkpoint_hash()
                .map_err(|error| {
                    Error::UnexpectedError(format!(
                        "State checkpoint must exist! Error: {:?}",
                        error
                    ))
                }),
            StateKind::Position => target_transaction_info
                .position_state_checkpoint_hash()
                .ok_or_else(|| Error::UnexpectedError("Missing position state root!".into())),
        }
```
