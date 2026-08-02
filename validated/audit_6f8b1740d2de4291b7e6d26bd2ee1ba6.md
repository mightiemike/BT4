This is a real, code-acknowledged gap in the replay-verification path: `TransactionOutput::ensure_match_transaction_info()` skips validating the state/hot-state/position checkpoint hashes.

### Title
`replay-verify` (`db-tool replay-on-archive`) accepts corrupted state-checkpoint roots because `ensure_match_transaction_info` never checks them - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info()` is the sole correctness gate used by `db-tool replay-on-archive` (and the CLI/debugger replay commands) to confirm that locally re-executed VM output matches the transaction info that was actually committed to the archived ledger. It checks status, gas, the write-set hash (`state_change_hash`), and the event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that bind the transaction info to the authenticated state/JMT root. The code contains its own TODO admitting this: "this comparator ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution."

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates:
- `status()` vs `txn_info.status()`
- `gas_used()` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()`

It never compares any locally-computed state-checkpoint root (main state SMT root, hot-state root, or the trading-native `position_state_checkpoint_hash`) against the corresponding field carried in the archived `TransactionInfo`. The TODO comment at lines 2197-2202 is explicit about this gap.

This function is the single verification primitive used by `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` ( [2](#0-1) ), which re-executes archived transactions and calls `ensure_match_transaction_info` per transaction to decide pass/fail for the whole verified range. It is also used from `aptos-move/cli/src/commands.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`.

Meanwhile, `TransactionInfo::V1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class, hash-covered fields (`types/src/transaction/mod.rs:2261-2284`), and the executor computes and commits these roots via `DoStateCheckpoint::run`/`get_state_checkpoint_hashes` ( [3](#0-2) ) into `TransactionInfo` through `DoLedgerUpdate::assemble_transaction_infos` ( [4](#0-3) ). These roots are exactly the values the accumulator subsequently commits and that later Merkle/state proofs (e.g. `get_state_proof_by_version_ext`, restore flows) trust as authoritative.

Because `ensure_match_transaction_info` never re-derives and compares these checkpoint roots, `replay-on-archive` — whose entire purpose is to detect divergence between a fresh VM execution and the previously-committed/archived ledger state — will report success even if the archived `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash` does not match what local re-execution actually produces. A bug in state-checkpoint/JMT root computation, a corrupted archive, or a maliciously altered backup snapshot that only tampers with the checkpoint-hash fields (while leaving write_set/event hashes intact) would go completely undetected by this tool.

### Impact Explanation
This breaks the "replay/restore must preserve deterministic proof binding" invariant called out in the state-integrity gate: `replay_on_archive` is the operational tool operators/auditors use to attest that an archived/backed-up ledger reflects correct VM execution. Silently accepting a wrong state-checkpoint root means:
- A corrupted or tampered archival/backup dataset (used for state-sync fast-sync bootstrapping or fork investigation) can pass verification even though its committed state root diverges from correct execution.
- Detection of consensus/hard-fork divergence in state-checkpoint computation (e.g. a bug in `DoStateCheckpoint`, hot-state root logic, or the newer position/trading-native root logic) is silently skipped by the one tool designed to catch it.

This is not a live mainnet consensus bypass (validators/full nodes independently verify LedgerInfo signatures via consensus, and the accumulator root itself still binds `TransactionInfo`), but it is a genuine proof/commitment-verification gap: an authenticated field (`state_checkpoint_hash`) that is supposed to be independently reproducible and checked is silently excluded from the tool's designated verification, defeating its stated purpose and potentially masking a hard-fork-class divergence in the state-checkpoint/JMT root computation.

### Likelihood Explanation
The gap is unconditional — it triggers on every invocation of `replay_on_archive` (and any other caller of `ensure_match_transaction_info`) for every V0/V1 transaction, with no special preconditions. It's already flagged in-repo as a known TODO, meaning the authors are aware but it has not been fixed, and no additional check compensates for it elsewhere in the replay path.

### Recommendation
Extend `ensure_match_transaction_info` to also compare locally-recomputed `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `txn_info`) against values produced by local execution, and thread through the required state-checkpoint hashes (as is already done for `expected_write_set`/`expected_events`) before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on `replay_on_archive` as an integrity gate.

### Proof of Concept
1. Take an archived transaction backup/state-snapshot manifest and modify only the `state_checkpoint_hash` field of a `TransactionInfo` V1 record (leaving `state_change_hash`/`event_root_hash` correct) to an arbitrary value while keeping the accumulator/signature chain otherwise self-consistent for testing purposes (or, in a test harness, directly construct a `TransactionOutput` whose local root differs from `txn_info.state_checkpoint_hash()`).
2. Run `db-tool replay-on-archive --start-version .. --end-version ..` against this data, or call `TransactionOutput::ensure_match_transaction_info` directly with a `TransactionInfo` carrying a mismatched `state_checkpoint_hash`.
3. Observe that `ensure_match_transaction_info` returns `Ok(())` despite the state-checkpoint root mismatch, at [5](#0-4) , because no comparison against `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` is performed — confirming replay-verify silently accepts the wrong committed state root.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L392-405)
```rust
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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-121)
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
```
