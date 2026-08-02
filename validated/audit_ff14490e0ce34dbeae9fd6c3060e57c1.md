### Title
`ensure_match_transaction_info` skips state-checkpoint/hot-state root comparison, letting replay-verify accept a wrong committed state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole per-transaction integrity check used by the `db-tool replay-on-archive` path (and other debugger tooling) to confirm that freshly re-executed VM output matches the transaction info recorded on the authenticated ledger (i.e., the archived, accumulator-committed `TransactionInfo`). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but — per its own inline TODO — deliberately omits comparing `state_checkpoint_hash` (and `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), the fields that carry the Sparse-Merkle-Tree state root produced by the executor. This means the one tool designed to detect state-root divergence during replay cannot detect it.

### Finding Description
`ensure_match_transaction_info` is defined at [1](#0-0)  and performs its comparisons at [2](#0-1) . The function explicitly documents the gap: [3](#0-2) 

It never reads or compares `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` against anything derived from the freshly executed state. This is the exact function called by `storage/db-tool/src/replay_on_archive.rs` as the terminal correctness check per replayed transaction: [4](#0-3) . The state root itself is computed in the normal executor pipeline by `DoStateCheckpoint::run` from `parent_state_summary.update(...)` and stored as `last_checkpoint.root_hash()` (and `hot_root_hash()` under `HOT_STATE_ROOT_IN_TXN_INFO`, and position roots under `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) at [5](#0-4) , then folded into `TransactionInfo` at [6](#0-5) . Because `ensure_match_transaction_info` never re-derives or compares this root, a bug anywhere in the SMT/hot-state/position-state update logic (feature-flag-gated code paths that are newer and less battle-tested, as flagged by the TODO referencing `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would silently pass replay-verify even though it produces a materially different state root than the one committed to the ledger accumulator.

### Impact Explanation
Replay-verify (`db-tool replay-on-archive`) and the `aptos-debugger`/`cli` callers of this same function are the primary automated tools used to detect execution/state divergence against the authenticated, accumulator-committed history — precisely the "replay ... proof-integrity" invariant called out in the assessment scope. A state-root computation bug (e.g., in the hot-state or new position-state summary logic gated by `HOT_STATE_ROOT_IN_TXN_INFO`/`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would go undetected by this tool because the comparator only checks `write_set` hash and event root, not the state checkpoint root that is supposed to reflect the post-execution global state. This satisfies "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Authenticated ... output bound to the wrong version, object, or proof context" since the check silently accepts a `TransactionOutput` whose actual resulting state tree does not match the one authenticated by the ledger's `TransactionInfo`.

### Likelihood Explanation
This is not a remotely triggerable consensus bug by itself — it is a verification-tool gap. Likelihood of exploitation depends on an independent state-root computation defect (e.g., in hot-state or position-state summary updates) existing and going undetected because this is the mechanism meant to catch it. The comment in the code itself acknowledges the gap is real and intentional ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), indicating the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is being rolled out without this safety net in place, which raises the chance that a real divergence bug ships silently for validator operators/auditors relying on `replay-verify` results.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, and (when present) `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, against caller-supplied expected values (or computed state summary roots), before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, exactly as the existing TODO instructs. At minimum, gate the feature flag rollout on closing this verification gap, or fail loudly/refuse to report success in replay-verify tooling when checkpoint hashes cannot be validated.

### Proof of Concept
Not independently demonstrable as a state-corruption exploit from this code alone — the finding is a verification-completeness gap, not a computation bug. Conceptual PoC: run `db-tool replay-on-archive` against an archive segment; construct/execute a scenario where the hot-state or position-state root ends up different from the one recorded on-chain (via a hypothetical divergent hot-state update), while `write_set`, gas, status and event root remain identical. `ensure_match_transaction_info` at [2](#0-1)  would return `Ok(())`, and `replay_on_archive.rs`'s `execute_and_verify` at [4](#0-3)  would report success despite the state-root divergence.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2145)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
```

**File:** types/src/transaction/mod.rs (L2159-2196)
```rust
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
