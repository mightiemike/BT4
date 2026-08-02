This confirms the gap is real and reachable in production tooling: `ensure_match_transaction_info` is the actual comparator invoked by `storage/db-tool/src/replay_on_archive.rs` (the tool run against mainnet archives to verify replay correctness) as well as `aptos-move/cli/src/commands.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`.

### Title
`TransactionOutput::ensure_match_transaction_info` never validates state/hot-state/position checkpoint hashes, letting replay-verify accept a diverged authenticated state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the canonical function used by replay/debug tooling to prove that a freshly re-executed transaction output matches the transaction info that was actually committed to the ledger (and thus covered by the accumulator/ledger-info signature). It validates status, gas, write-set hash, and event root hash, but it does not validate `TransactionInfo::state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` at all [1](#0-0) . This is explicitly acknowledged in a TODO left directly above the function's `Ok(())` return, warning that "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution" [2](#0-1) .

### Finding Description
`ensure_match_transaction_info` is called by `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, which is the production tool operators run to independently re-execute mainnet history from a backup archive and confirm the recomputed `TransactionOutput` for each version matches the `TransactionInfo` that was actually signed by validators in the corresponding `LedgerInfo` [3](#0-2) . The same comparator is reused by `aptos-move/cli/src/commands.rs` (transaction replay CLI) and `aptos-move/aptos-debugger/src/aptos_debugger.rs::print_mismatches` for one-off debugging/replay [4](#0-3) [5](#0-4) .

`TransactionInfo` carries multiple checkpoint-hash fields beyond the write-set/event hashes that are checked: `state_checkpoint_hash`, `hot_state_checkpoint_hash` (used across state/hot-state Merkle summary construction, see `execution/executor/src/workflow/do_state_checkpoint.rs`), and the newer `position_state_checkpoint_hash` used for the "trading-native" native-position SMT summary [6](#0-5) . These checkpoint hashes are the authenticated commitments to the post-execution state tree (and, once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, to the position tree) — i.e., they are exactly the kind of "wrong accumulator/Merkle root accepted as valid" surface called out as in-scope. Because `ensure_match_transaction_info` silently skips comparing any of them, a version whose write set and events hash correctly but whose derived state (or hot-state, or native-position) root diverges from what was actually committed and signed will still be reported as a full match by every caller of this function.

### Impact Explanation
This breaks the state-commitment/proof-integrity invariant that "committed state that differs from the correct VM result" must be detectable during replay/verification. `replay_on_archive` and CLI-based transaction replay are the primary tools used to detect state divergence (e.g., during a hard fork, a bug in VM/native execution, or a storage bug that produces a wrong state tree) after the fact by comparing recomputation against the authenticated, validator-signed `TransactionInfo`. With this gap, any divergence confined to the state-checkpoint/hot-state/position-checkpoint hash — without touching the write-set hash, event hash, gas, or status — passes verification silently. This is a hard-fork-detection blind spot: a bug that corrupts the derived state root (but reproduces the same write set) would go completely unnoticed by the tooling meant to catch exactly that class of issue, directly undermining confidence in "authenticated" state roots on mainnet.

### Likelihood Explanation
The gap is unconditional and always present in the current code — every call to `ensure_match_transaction_info` skips these checks. It becomes actively dangerous once `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/native-position checkpointing ships, since `position_state_checkpoint_hash` is the authenticated root of a new state summary computed via a partially separate code path (`DoStateCheckpoint`), which is exactly the kind of newly-added logic prone to divergence bugs; the code comment itself flags this as a known but unresolved risk that must be fixed before that feature is fully enabled.

### Recommendation
Extend `ensure_match_transaction_info` to compare `self`-derived state checkpoint hash(es) against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` (when available/enabled), following through on the TODO before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or hot-state checkpointing is fully relied upon in production replay-verify flows.

### Proof of Concept
Not exploitable via a transaction script; the issue is a static code-review finding. Any test that constructs a `TransactionOutput`/`TransactionInfo` pair with identical write sets, events, gas, and status but differing `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) values demonstrates `ensure_match_transaction_info` returning `Ok(())` — e.g. calling it from `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` on such a synthetic pair still reports a successful match at [7](#0-6) , mirroring the exact scenario the in-code TODO warns about at [2](#0-1) .

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-405)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L179-234)
```rust
        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;

        let summary =
            LedgerWithSummary::from_latest_and_last_checkpoint(new_latest, new_last_checkpoint);
        Ok((summary, hashes))
    }

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
