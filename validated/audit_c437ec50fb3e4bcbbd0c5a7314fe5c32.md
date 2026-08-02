Found a concrete state-integrity gap that matches the required proof-verification invariant: transaction-info re-verification during replay silently skips comparing the state-checkpoint root hashes.

### Title
Replay-verification (`ensure_match_transaction_info`) never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash` against `TransactionInfo`, letting a diverged state root pass as verified - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a freshly re-executed `TransactionOutput` matches the authenticated `TransactionInfo` recorded on-chain (i.e., stored in the transaction accumulator and covered by the ledger-info signature). It checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash` (the Sparse-Merkle/JMT state root) or `hot_state_checkpoint_hash`, even though these fields exist in the same `TransactionInfo` and are meant to bind the transaction to the correct global state root.

### Finding Description [1](#0-0) 

The function performs `ensure!` checks for status, gas, write-set hash, and event root hash, but ends with only a comment:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
No comparison of `txn_info.state_checkpoint_hash()` or `txn_info.hot_state_checkpoint_hash()` against a locally recomputed state root is performed anywhere in this method. This is not gated behind an unreleased feature flag for the base `state_checkpoint_hash`/`hot_state_checkpoint_hash` fields — those are populated on every checkpoint transaction on mainnet today via `assemble_transaction_infos` [2](#0-1)  and `do_state_checkpoint.rs` [3](#0-2) . Only the newer `position_state_checkpoint_hash` is behind the not-yet-enabled trading-native flag; the state/hot-state checkpoint hashes are core, always-on fields that this function was already supposed to validate.

This function is the sole state-consistency gate used by:
- `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which re-executes archived transactions and is meant to catch any divergence between locally computed VM output and the authenticated on-chain `TransactionInfo` [4](#0-3) .
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`'s mismatch printer [5](#0-4) .
- `aptos-move/cli/src/commands.rs`'s transaction replay command [6](#0-5) .

Because the state root fields are skipped, a local re-execution that produces a state root divergent from the authenticated `TransactionInfo.state_checkpoint_hash`/`hot_state_checkpoint_hash` (e.g., due to a state-store bug, a hard-fork/non-determinism, storage corruption, or a JMT/state-view regression that only surfaces in the resulting root but not in the write-set bytes themselves) will be reported by `replay_on_archive` as a **successful, verified replay**, even though the state commitment is actually wrong.

### Impact Explanation
`replay_on_archive` and the debugger's replay path are the operational tools relied on to detect exactly this class of bug: a validator/full-node computing a different state root than what was actually committed and signed by consensus. Because the comparator silently omits the state-checkpoint hash fields, this verification tool provides false assurance — it cannot detect state-root divergence, defeating its core security purpose (an authenticated proof-context binding failure per the Proof And Storage Pivots criteria: "Storage schemas, replay paths ... must not reinterpret committed data into a different ledger state"). This directly weakens detection of hard-fork-class divergence during replay/restore verification, which is explicitly listed as in-scope impact.

### Likelihood Explanation
The bug is code-confirmed (not speculative) via the developer's own TODO comment acknowledging the exact failure mode. It requires no attacker action to trigger the detection gap — it is a standing, deterministic gap in every invocation of `ensure_match_transaction_info`; the only requirement for actual harm is a state-root divergence occurring for any reason (implementation bug, non-determinism, storage bug), at which point this verification path would fail to flag it.

### Recommendation
In `ensure_match_transaction_info`, add explicit checks comparing a locally recomputed state root (and hot-state root, where applicable) against `txn_info.state_checkpoint_hash()` / `txn_info.hot_state_checkpoint_hash()` whenever the transaction is a checkpoint boundary (i.e., whenever `txn_info.state_checkpoint_hash()` is `Some`), consistent with how `write_set_hash` and `event_root_hash` are already validated. Only `position_state_checkpoint_hash` should remain conditionally validated behind the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` flag as the comment suggests.

### Proof of Concept
1. Run `replay_on_archive` (or the debugger) against a version range that includes a checkpoint transaction.
2. Artificially or naturally produce a locally re-executed `TransactionOutput` whose resulting state root differs from `expected_txn_infos[idx].state_checkpoint_hash()` (write-set bytes and events can remain identical to the authenticated info while the derived state root differs, e.g. via a state-view/backing-store discrepancy not reflected in the write set diff itself).
3. Observe that `execute_and_verify` in `storage/db-tool/src/replay_on_archive.rs` calls `ensure_match_transaction_info` [7](#0-6)  and receives `Ok(())` despite the state-root divergence, because the method never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-106)
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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
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

**File:** aptos-move/cli/src/commands.rs (L2651-2655)
```rust
                if !self.skip_comparison {
                    txn_output
                        .ensure_match_transaction_info(self.txn_id, &txn_info, None, None)
                        .map_err(|msg| CliError::UnexpectedError(msg.to_string()))?;
                }
```
