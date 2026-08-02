### Title
Replay-verify's `ensure_match_transaction_info` does not validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`, allowing a divergent, unauthenticated state root to pass replay verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/backup verification tooling (`db-tool`'s `replay_on_archive`, `aptos-debugger`, `execution/executor/src/chunk_executor`) to confirm that locally re-executed transaction results match the authenticated `TransactionInfo` pulled from a backup or archive. As implemented, it only checks `status`, `gas_used`, `state_change_hash` (write-set hash), and `event_root_hash`. It explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that carry the Merkle roots of the actual world state / hot state / native-position state at that version.

### Finding Description [1](#0-0) 

The function compares only four of the seven fields carried by `TransactionInfo`/`TransactionInfoV1`:
- `status` vs `expected_txn_status`
- `gas_used`
- `write_set_hash` vs `state_change_hash`
- `event_root_hash`

It never re-derives or compares `state_checkpoint_hash` (the Sparse-Merkle/Jellyfish-Merkle state root), `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against locally computed roots. The code contains a self-documenting acknowledgment of this gap: [2](#0-1) , which states verbatim that "this comparator ignores the checkpoint hashes … so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This function is consumed directly by the replay-verification/backup-restore path: [3](#0-2)  calls `execute_and_verify`, which is the consumer of `ensure_match_transaction_info`, and `ensure_match_transaction_info` is also invoked from `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `execution/executor/src/chunk_executor/mod.rs`. The commit path itself does separately compute the checkpoint hash and enforce it matches expectations during normal execution (`DoStateCheckpoint::get_state_checkpoint_hashes` asserts equality against `known_state_checkpoints` when supplied — [4](#0-3) ), but `ensure_match_transaction_info` in `types/src/transaction/mod.rs` is a separate, weaker check used specifically in replay/backup verification flows, and it is this weaker check that omits the checkpoint hash comparisons.

### Impact Explanation
State-checkpoint hashes are the authenticated binding between a version's `TransactionInfo` (committed into the transaction accumulator and signed by validators via `LedgerInfo`) and the actual Sparse/Jellyfish Merkle root of world state at that version. If a bug in execution, state-checkpoint computation, or the new native-position/hot-state root computation (guarded by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) produces a state root that differs from the one recorded on-chain, `ensure_match_transaction_info` will not catch it because it never compares `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`. Replay-verify (used to validate archive/backup integrity and to catch consensus-breaking bugs before they reach mainnet) would report success despite this divergence, defeating its core purpose as the last line of defense for detecting state-commitment bugs. This falls squarely within the "wrong accumulator root ... accepted as valid" and "restore paths must preserve deterministic proof binding" categories of the state-integrity gate.

However, the impact is currently **latent rather than exploitable on mainnet today**: the affected fields (`hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) are gated behind on-chain feature flags (`HOT_STATE_ROOT_IN_TXN_INFO`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that determine whether these hashes are even populated (`Option<HashValue>` is `None` otherwise), and the code comment itself frames this as a TODO to be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." I could not confirm from the available index whether `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` are currently enabled on mainnet — that would need to be checked against `aptos_features.rs` on-chain default state and governance history, which I don't have full visibility into via the index.

Note: even the base `state_checkpoint_hash` field (which exists in `TransactionInfoV0` and has always been part of consensus) is not compared here — this is not new/feature-gated behavior for that specific field, meaning the checkpoint-hash-skipping behavior in this replay-verify comparator predates the trading-native work and is broader than just the newly gated fields.

### Likelihood Explanation
Likelihood is **Medium-Low**: exploiting this gap requires (a) a separate, independent state-computation bug elsewhere in execution/checkpoint code that produces a wrong state root, and (b) reliance on replay-verify tooling to catch it. This weakness does not itself corrupt state or accept a bad proof in the normal validation/consensus path (the transaction accumulator and `LedgerInfo` signature checks elsewhere still bind the full `TransactionInfo` including checkpoint hashes); it only weakens a secondary verification tool meant to catch such bugs during backup/replay. Its severity is real only in combination with another root-cause bug, which I did not find independently in this pass — this reduces it to a detection-gap rather than a directly exploitable primitive.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` whenever these are present (`Some`) in `txn_info`, comparing them against locally recomputed roots, so that replay-verify tooling cannot silently accept a state root divergence. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, as the existing code comment already recommends.

### Proof of Concept
Not independently reproducible from static analysis alone — the gap is a comparator omission, self-documented in the code, rather than a demonstrated wrong-root computation. A full PoC would require constructing a divergent local state root (e.g., via a hot-state/position-state computation bug) and showing `replay_on_archive`/`execute_and_verify` returns success despite the mismatch; this requires runtime access to the executor and backup tooling that is outside what I can verify via the code index alone.

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
