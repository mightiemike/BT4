## Title
Replay-verification bypass of authenticated position-state (trading-native) root via `ensure_match_transaction_info()` — (File: `types/src/transaction/mod.rs`)

## Summary
`TransactionOutput::ensure_match_transaction_info()` is the invariant that binds a locally-computed `TransactionOutput` to the authenticated on-chain `TransactionInfo` (the leaf committed into the transaction accumulator and covered by the ledger-info signature). It checks status, gas, write-set hash, and event root, but — by its own admission in a code comment — does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. [1](#0-0) 

## Finding Description
`ensure_match_transaction_info` compares only status, gas, write-set hash and event root hash between a replayed/locally-produced `TransactionOutput` and the authenticated `TransactionInfo`: [2](#0-1) 

The function's own trailing comment states the gap explicitly: [3](#0-2) 

This is the function used by the `aptos-debugger`/CLI replay path (`aptos-move/cli/src/commands.rs`) to assert that a locally re-executed transaction matches the committed, ledger-info-authenticated `TransactionInfo`: [4](#0-3) 

`TransactionInfo::V1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` as first-class, ledger-info-committed fields (constructed via `builder_v1`): [5](#0-4) 

These roots are populated and verified against locally computed values inside the executor's real commit path (`DoStateCheckpoint::run` / `get_state_checkpoint_hashes`), but only when the relevant on-chain flag is on: [6](#0-5) [7](#0-6) 

So in the primary chunk-executor/state-sync commit path, position/hot-state roots *are* independently recomputed and checked against the incoming `TransactionInfo`s (`known_position_state_checkpoints`/`known_hot_state_checkpoints`) — that path is not broken. The break is isolated to `ensure_match_transaction_info`, which is the comparator used by replay/debug tooling (`aptos-debugger`/CLI's transaction-replay command) to assert that a re-executed transaction's `TransactionOutput` matches the archived, signed `TransactionInfo`. Because it silently skips the checkpoint-hash fields, a replay can be reported as "matching" even though the position-state (trading-native) root, hot-state root, or plain state-checkpoint root diverge from what was actually committed and signed by validators.

## Impact Explanation
This breaks the "committed state that differs from the correct VM result... accepted as matching" invariant for the specific proof-and-storage pivot called out in the task: *"Committed state that differs from the correct VM result or corrupts durable ledger data"* and *"Hard-fork-only divergence during commit, replay, restore, or proof verification."* Any tooling, auditor, or future consensus/replay-verification job that relies on `ensure_match_transaction_info` (as the CLI replay command does today) to assert "my local re-execution matches the authenticated chain history" will get a false positive when only the state/hot-state/position-state checkpoint hash diverges, masking an actual state-commitment divergence (e.g. from a bug in the trading-native/position state computation, or from a malicious/buggy fork). This is exactly the kind of authenticated-output/root binding gap the exercise's "Proof And Storage Pivots" section targets.

That said, the severity is bounded: this is a comparator used by an offline debugging/replay tool, not a live consensus, state-sync, or API-serving code path — the actual on-chain commit path (`DoStateCheckpoint`) independently validates these roots when the corresponding feature is enabled, so mainnet consensus safety is not directly compromised by this gap alone. The risk materializes specifically when the trading-native/position-state feature (`compute_trading_native_state_roots`) is enabled and someone relies on this comparator to validate replay correctness — the divergence would go undetected.

## Likelihood Explanation
Medium-low today: `compute_trading_native_state_roots`/hot-state root features exist as on-chain-configurable flags (see `types/src/block_executor/config.rs` and `types/src/transaction/mod.rs` TransactionInfoV1 fields) but the gap only bites once the feature is turned on and someone depends on `ensure_match_transaction_info` for correctness assurance rather than manual/independent verification. The author already flagged this in a TODO comment, indicating it is a known, not-yet-fixed gap rather than a hypothetical one — but it requires the feature flag to be live and the omitted-check code path to be relied upon before it produces a concrete undetected divergence.

## Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed checkpoint roots (mirroring the checks already done in `DoStateCheckpoint::get_state_checkpoint_hashes`), gated appropriately by whichever on-chain flags are active for the version being replayed, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled on mainnet.

## Proof of Concept
1. Enable (or simulate) `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state-root-in-txn-info on a network, so `TransactionInfo::V1` carries non-trivial `position_state_checkpoint_hash`/`hot_state_checkpoint_hash`.
2. Introduce (or naturally hit, via a real bug) a divergence in local position-state/hot-state computation that does not affect the write set, events, gas, or status (e.g., a bug isolated to `DoStateCheckpoint::compute_position_checkpoint` in `execution/executor/src/workflow/do_state_checkpoint.rs`).
3. Run the `aptos-debugger`/CLI replay command against the affected transaction (`aptos-move/cli/src/commands.rs:2797-2813`), which calls `txn_output.ensure_match_transaction_info(...)`.
4. Observe that the call returns `Ok(())` despite the position/hot-state root diverging from the authenticated `TransactionInfo`, because `ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) never compares those fields — confirming the false "replay matches" result documented by the code's own TODO.

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
