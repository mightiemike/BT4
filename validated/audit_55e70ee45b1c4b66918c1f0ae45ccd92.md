## Finding

### Title
Replay-verify tooling never checks `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, silently accepting a divergent state root as a valid replay - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole state-integrity gate used by history-replay/verification tools (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/cli/src/commands.rs` debugger replay) to confirm that locally re-executed transactions match the transaction infos committed to the authenticated transaction accumulator. It checks `status`, `gas_used`, `write_set` hash (`state_change_hash`), and `event_root_hash`, but explicitly skips comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that bind the replay to the actual Sparse-Merkle/JMT state root. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` computes and asserts equality only for `write_set_hash` and `event_root_hash` against `txn_info.state_change_hash()` / `txn_info.event_root_hash()`; the checkpoint-hash fields are read nowhere in the function, and a code comment explicitly documents the gap: [2](#0-1) 

This function is used directly (without any supplementary state-root comparison) by:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify` path, which is a production tool for verifying a target DB's history against independently re-executed transactions, [3](#0-2) 
- `aptos-move/cli/src/commands.rs` debugger replay flows for both system and user transactions. [4](#0-3) [5](#0-4) 

By contrast, the *live* chunk executor path (`execution/executor/src/chunk_executor/mod.rs::update_ledger` / `verify_execution`) does feed `known_state_checkpoints` derived from the committed `TransactionInfo` into `DoStateCheckpoint::run`, so the state root is validated there independently of `ensure_match_transaction_info`. [6](#0-5) 

The distinction matters: `execute_and_verify`/`db-tool replay_on_archive` and the CLI debugger do not go through `DoStateCheckpoint`; `ensure_match_transaction_info` is their only correctness gate. Since it omits the checkpoint-hash comparison, any divergence confined to state-root computation (e.g., a bug in Sparse Merkle Tree construction, JMT node hashing, or a future position-state-checkpoint computation bug affecting `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that still produces an identical write set and event set would be reported as a **successful** replay/verify.

### Impact Explanation
This breaks the "authenticated API or state-view output bound to the wrong version/root" and "proof/commit verification accepted as valid despite wrong root" invariants named in the state-integrity gate: a replay-verify run (used operationally to confirm archived/backed-up chain history integrity, and by the CLI to sanity-check re-executed transactions against on-chain data) can report success even though the locally computed state root diverges from the one anchored in the ledger's authenticated `TransactionInfo`/accumulator. This masks state-commitment bugs precisely in the class of code (state-merklization / checkpoint hashing) that this tool exists to catch, undermining confidence in replay-verify results used for auditing mainnet history and for enabling the (currently gated) `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature.

### Likelihood Explanation
The gap is unconditional (not just gated behind `COMPUTE_TRADING_NATIVE_STATE_ROOTS`): `state_checkpoint_hash` is a pre-existing field of `TransactionInfoV0`/`V1` that is never compared in this function at all, for any transaction, in any of its call sites. Triggering the false-positive requires a state-root computation bug to exist elsewhere (this function does not itself corrupt state on mainnet commit — the live executor path is unaffected), so likelihood is contingent on such a bug existing and being exercised specifically on the replay-verify/debugger tool paths. This is why I present it as a **detection/verification-integrity gap** rather than a live consensus-breaking bug: I could not find, within available time, a companion bug in JMT/SMT construction that would actually produce a divergent-but-undetected root today. The code comment itself confirms the aptos engineers are already aware and intend to close the gap before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Recommendation
Have `ensure_match_transaction_info` also compare `self`'s recomputed state-checkpoint-related roots (or, at minimum, refuse silent success and require the caller to independently verify `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` when available) so that `db-tool replay_on_archive` and CLI debugger replay cannot report a clean pass while the state root has diverged.

### Proof of Concept
Not directly exploitable as a standalone PoC without an accompanying state-root-divergence bug; the observable defect is: construct/replay a transaction whose resulting write set and events are unchanged but whose Sparse-Merkle-Tree/JMT root differs from the one recorded on-chain (e.g., by asserting a manually altered `TransactionInfo.state_checkpoint_hash` in a test call to `ensure_match_transaction_info`) — the call returns `Ok(())` because the checkpoint-hash fields are never inspected, confirming the missing check at [7](#0-6) .

---
**Caveat:** I was unable, within the remaining iterations, to locate a distinct root-cause bug in the JMT/SMT construction path that would actually produce a divergent state root while keeping the write set/events identical — which is required to escalate this from a "verification tool blind spot" to a full "wrong state accepted as valid on mainnet" finding per the Gate's criteria. Given that limitation, this should be treated as a verification-tooling integrity gap (explicitly acknowledged in a TODO by the codebase itself) rather than a confirmed High/Critical live vulnerability, and reported with that caveat.

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

**File:** storage/db-tool/src/replay_on_archive.rs (L285-296)
```rust
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
