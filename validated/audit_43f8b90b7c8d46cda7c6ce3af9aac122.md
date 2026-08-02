## Title
`replay_on_archive` verifier accepts a corrupted/divergent state root because `ensure_match_transaction_info` never checks any state-checkpoint hash - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness check used by the standalone `db-tool replay-on-archive` verifier (`storage/db-tool/src/replay_on_archive.rs`) to confirm that locally re-executed transactions match the archived, ledger-signed `TransactionInfo`. This function checks status, gas, write-set hash (`state_change_hash`), and event root hash, but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the corresponding locally computed roots. [1](#0-0) [2](#0-1) 

### Finding Description
`ensure_match_transaction_info` compares:
- execution status vs. `txn_info.status()`
- `gas_used` vs. `txn_info.gas_used()`
- `write_set` hash vs. `txn_info.state_change_hash()`
- event root hash vs. `txn_info.event_root_hash()` [3](#0-2) 

It deliberately skips the state/hot-state/position checkpoint hashes, as documented by the code's own TODO: [4](#0-3) 

This function is the *only* validation step in `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`/`verify` path, which is a standalone tool that re-executes archived transactions with `AptosVMBlockExecutor` and cross-checks against `expected_txn_infos` pulled straight from a backup archive: [5](#0-4) 

Unlike the normal chunk-executor commit/replay pipeline used by `ChunkExecutor::update_ledger`, which independently derives and asserts state-checkpoint hashes via `DoStateCheckpoint::run` -> `get_state_checkpoint_hashes` (which does `ensure!(known[idx] == Some(computed_last_checkpoint_hash), …)`): [6](#0-5) 

the `replay_on_archive` tool never invokes `DoStateCheckpoint`/`DoLedgerUpdate` at all — it only calls VM execution and `ensure_match_transaction_info`. Consequently, this tool can report "replay verified OK" for a version whose JMT state root, hot-state root, or (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) the trading-native position-state root diverges from the value actually committed and signed into the ledger's `TransactionInfoV1`. The exact same integrity gap is called out inline in the codebase's own comment on `ensure_match_transaction_info`, confirming this is a known, unresolved bug rather than a speculative one.

### Impact Explanation
`replay_on_archive` exists specifically to give operators/auditors independent confidence that a backup archive's committed state matches locally re-executed VM results — an authenticated-replay integrity guarantee. Because the check silently omits the state-checkpoint/root fields, a state root corrupted upstream (e.g., from a storage bug, a malicious archive provider, or a divergent VM/feature-flag configuration that happens to still produce matching write-set hash, gas, status and event root but a different state tree) would pass verification undetected. This is a "wrong ... state proof accepted as valid" / "authenticated ... state-view output bound to the wrong ... proof context" class of issue per the stated required impacts: an operator relying on this tool to detect ledger divergence gets a false "all good" signal on mainnet archive data, particularly relevant once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled and their per-transaction checkpoint hashes become part of consensus-verified `TransactionInfoV1`.

### Likelihood Explanation
This does not require any privileged access — it is a gap in an always-invoked, unprivileged validation routine reachable by any operator running the tool against untrusted or corrupted archive/backup data (the intended threat model for this tool). It is deterministic and 100% reproducible: any divergence confined to the state/hot-state/position checkpoint hash (while write-set hash, gas, status, and events happen to match, e.g. due to non-deterministic-but-cancel-out storage bugs, upgrade/migration bugs, or a malicious backup source) is silently accepted. The likelihood of the state-checkpoint hash diverging while all four checked fields coincidentally match is the main mitigating factor, but the check is structurally absent regardless — it is not a probabilistic gap, it's a missing invariant.

### Recommendation
Extend `TransactionOutput::ensure_match_transaction_info` (or add a companion check invoked by `replay_on_archive`) to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed roots at checkpoint boundaries, mirroring the `get_state_checkpoint_hashes` logic already used in the main chunk-executor pipeline. At minimum, `replay_on_archive`'s verifier should route through `DoStateCheckpoint`/`DoLedgerUpdate` (or equivalent) rather than relying solely on `ensure_match_transaction_info`, so that a full independent re-derivation of the accumulator-committed roots occurs before declaring a version "verified."

### Proof of Concept
1. Prepare an archive/backup where a single version's persisted `TransactionInfoV1.state_checkpoint_hash` (or `position_state_checkpoint_hash`) has been altered to a wrong root hash, while `state_change_hash` (write-set hash), `event_root_hash`, `gas_used`, and `status` are left untouched/valid.
2. Run `db-tool replay-on-archive verify` against this archive.
3. `Verifier::execute_and_verify` re-executes the transaction, builds a `TransactionOutput`, and calls `ensure_match_transaction_info`, which only checks status/gas/write-set-hash/event-root-hash. [1](#0-0) 
4. Because none of these four checked fields were tampered with, `ensure_match_transaction_info` returns `Ok(())`, and the tool reports the version as successfully verified — despite the state (or position) checkpoint hash mismatching the true VM-computed root, which downstream `DoStateCheckpoint::get_state_checkpoint_hashes` (used elsewhere in the codebase) would have rejected. [7](#0-6)

### Citations

**File:** types/src/transaction/mod.rs (L2139-2195)
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
```

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

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
