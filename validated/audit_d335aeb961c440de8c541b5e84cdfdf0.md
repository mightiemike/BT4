### Title
`TransactionOutput::ensure_match_transaction_info` never checks `state_checkpoint_hash` (or the hot-state / position-state checkpoint hashes), so archive replay-verify tooling can certify a mismatched state root as correct - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) is the function used by offline/authenticated replay tooling to confirm that a locally re-executed `TransactionOutput` matches an archived, previously-committed `TransactionInfo`. It checks status, gas used, the write-set hash (`state_change_hash`), and the event root hash, but it never compares the `state_checkpoint_hash` (the JMT/state Merkle root committed at checkpoint boundaries), nor the `hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields that exist on `TransactionInfoV1`. [1](#0-0) 

### Finding Description
`TransactionInfo` carries `state_checkpoint_hash` as one of the fields committed into the transaction accumulator and ultimately signed by validators inside `LedgerInfo` [2](#0-1) . This hash is what proves that the state tree produced by execution is the one blessed by consensus — it is exactly the kind of "state proof" this task asks about.

`ensure_match_transaction_info` is the authenticated comparison point that offline verification tools use to assert "the transaction output I just computed matches the one already recorded/committed for this version." Looking at its body, it validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It does **not** call `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` anywhere. The code even contains an explicit acknowledgment of this gap:

```rust
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [3](#0-2) 

This function is used from `storage/db-tool/src/replay_on_archive.rs` (an operator/security tool whose entire purpose is to independently re-execute archived transactions and confirm the recorded, signed `TransactionInfo` is correct) [4](#0-3) , and also from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`. None of these callers independently re-derive the state Merkle root from the replayed write set and cross-check it against `txn_info.state_checkpoint_hash()` before declaring the replay "matched." (I was not able to fully confirm within the remaining iterations whether any of these three call sites separately recompute and compare the state checkpoint hash through some other code path outside of `ensure_match_transaction_info`; this needs further verification with direct access to the full call sites, which the index did not fully expose for `aptos_debugger.rs` and `commands.rs`.)

By contrast, the *live* chunk-executor path (`execution/executor/src/chunk_executor/mod.rs::update_ledger`) does thread `known_state_checkpoints` derived from `txn_info.state_checkpoint_hash()` into `DoStateCheckpoint::run`, which does assert the recomputed root against the known hash [5](#0-4) [6](#0-5) . So the state-sync/chunk-commit path is protected. The gap is specifically in the standalone `ensure_match_transaction_info` comparator used by the offline/archive replay-verify tooling, which is the tool an operator or auditor would run to *independently* confirm mainnet history is correct without going through the full chunk-executor pipeline.

### Impact Explanation
If a validator or archive node's stored `state_checkpoint_hash` in `TransactionInfo` for some historical version were ever wrong (e.g., due to a storage corruption, a state-restore bug, or a subtle non-determinism bug elsewhere in execution), `replay_on_archive`/`aptos-debugger` replay-verify tooling — the exact tool meant to catch such divergences — would report the replay as successful as long as status, gas, write-set hash, and event root happen to match, silently accepting a wrong, but already-signed, state root as "verified." This directly matches the required impact class "Wrong ... state proof accepted as valid" and "Authenticated API or state-view output bound to the wrong version, object, or proof context," because the verification tool is the authenticated consumer of `TransactionInfo` and it fails to bind its check to the state-root field.

The severity is limited by two factors I could not fully rule out in the time available: (1) whether `state_change_hash` (write-set hash) by itself is a good enough proxy for state correctness in practice — it is not, since `state_checkpoint_hash` is a Merkle root over the entire live state tree (dependent on account/resource state after applying possibly many prior writes), which is a materially different invariant than the write set of a single transaction; and (2) the position/hot-state checkpoint hash gap is explicitly gated behind not-yet-enabled features (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`), so that portion of the gap has no live mainnet blast radius today. The `state_checkpoint_hash` omission, however, applies unconditionally to `TransactionInfoV0` and `V1` alike and is not behind any feature flag.

### Likelihood Explanation
This is not a live exploit against consensus or committed ledger state on its own — the write path (chunk executor / consensus) does validate state checkpoint hashes correctly. The exposure is specifically that the *verification tool* used to catch divergences after the fact has an incomplete assertion. Triggering meaningful impact requires an independent root cause elsewhere (a bug that produces a wrong state root while still preserving the write-set hash, gas, status, and event root — which is plausible since state_checkpoint_hash also depends on prior versions' cumulative state, not just this transaction's write set). I could not, within the available tool budget, find or rule out such a root cause bug elsewhere in this codebase revision, so I can't assert the compound scenario (wrong root actually produced) is currently reachable; only that the *detection mechanism* for it in `ensure_match_transaction_info` is broken by omission.

### Recommendation
Add checks in `TransactionOutput::ensure_match_transaction_info` (or in each of its three call sites) that independently recompute the state (and, where applicable, hot-state/position-state) checkpoint root at checkpoint boundaries and assert it equals `txn_info.state_checkpoint_hash()` (and the corresponding V1 fields), mirroring what `DoStateCheckpoint::get_state_checkpoint_hashes` already does in the live chunk-executor path [7](#0-6) .

### Proof of Concept
Not applicable as an end-to-end mainnet exploit within the scope investigated: the finding is a code-level assertion gap (missing check) in `ensure_match_transaction_info`, demonstrable by inspection — construct a `TransactionOutput` whose `write_set`, `events`, `status`, and `gas_used` match an existing `TransactionInfo`, but whose implied resulting state Merkle root would differ (e.g., an execution path with a non-deterministic-but-hash-matching write set applied on top of a different underlying persisted state). Calling `ensure_match_transaction_info` on this pair returns `Ok(())` because `state_checkpoint_hash` is never consulted. I was not able, within the remaining iterations, to construct or confirm a concrete way to make the VM/state layer actually produce such a divergent-but-matching output, so this should be treated as a **verification-logic gap** finding rather than a demonstrated end-to-end state-corruption exploit — flagging this uncertainty explicitly rather than overstating confidence.

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

**File:** types/src/transaction/mod.rs (L2196-2203)
```rust

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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

**File:** storage/db-tool/src/replay_on_archive.rs (L242-293)
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
