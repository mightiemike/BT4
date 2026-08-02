This is a genuine, self-documented gap in the codebase — the `TransactionOutput::ensure_match_transaction_info` comparator, which replay-verification tooling relies on to confirm that VM re-execution matches previously committed `TransactionInfo`, silently skips validating the state-checkpoint-related hash fields.

### Title
Replay-verification comparator skips state/hot-state/position checkpoint hash checks, allowing corrupted state roots to pass verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authoritative comparator used by replay/debug tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`) to confirm that re-executing a transaction with the VM reproduces the transaction status, gas, write-set hash, and event root that were already committed on-chain in the `TransactionInfo` stored in the ledger. It deliberately does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself flags with a `TODO`.

### Finding Description
`ensure_match_transaction_info` checks status, gas used, write-set hash, and event root hash against the target `TransactionInfo`, but explicitly returns `Ok(())` without ever checking the state-checkpoint hash fields carried by `TransactionInfoV0`/`TransactionInfoV1`: [1](#0-0) 

The comment in the code states this precisely: [2](#0-1) 

This function is the sole verification primitive used by:
- `storage/db-tool/src/replay_on_archive.rs`, in `Verifier::execute_and_verify`, which re-executes historical blocks and calls `ensure_match_transaction_info` to decide whether replay matches the archived ledger: [3](#0-2) 
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (both call the same function for transaction replay verification).

Meanwhile, the real state-checkpoint root computed during normal block execution/commit (`DoStateCheckpoint::run`) is only validated against "known" hashes passed in from persisted `TransactionInfo` during chunk-executor restore/sync flows, not during this ad-hoc replay-verify path: [4](#0-3) 

Because `position_state_checkpoint_hash` support (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is a newer, feature-gated addition, and the general state/hot-state checkpoint hashes are also skipped, any divergence between the VM's freshly computed checkpoint root and the one already committed to the ledger will not be detected by this comparator.

### Impact Explanation
This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "authenticated ... state-view output bound to the wrong version, object, or proof context" categories: a node operator or auditor running `replay_on_archive`/`aptos-debugger` to verify that historical execution is reproducible (e.g., before a hard fork, or to detect state-tree corruption/non-determinism bugs) will get a false "verification passed" result even if the recomputed state/hot-state/position Merkle root diverges from what was actually committed. This masks exactly the kind of consensus-breaking state divergence that replay-verify is meant to catch, and is particularly dangerous for the new `position_state_checkpoint_hash` root, which per the code comment already has a live TODO indicating it is currently unverified end-to-end by this tooling.

### Likelihood Explanation
The gap is unconditional (not behind an error path) — every call to `ensure_match_transaction_info` skips these checks regardless of feature flags, so any bug in the write-set-to-state application logic, the Jellyfish Merkle update, or the position-state SMT logic that alters the checkpoint root without altering gas/status/write-set/event hashes would go undetected by replay-verify. Likelihood of exploitation as a stand-alone attack is low (this is a verification-tool gap, not a live consensus-commit gap, since actual block commit still separately computes/binds the checkpoint hash via `DoStateCheckpoint`), but likelihood of masking a real state-divergence bug during incident response/hard-fork prep is meaningful given the explicit self-documented TODO.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on both sides) against the recomputed checkpoint output, so that all replay/debug verification tooling actually validates full ledger-state equivalence, not just write-set/event/gas/status equivalence.

### Proof of Concept
Not applicable as a live network exploit — the issue is demonstrated purely by code inspection: construct/replay any block where the VM output write-set hash, event hash, gas, and status match the stored `TransactionInfo` but the resulting state/hot-state/position checkpoint root differs (e.g., due to a Merkle-tree update bug); `ensure_match_transaction_info` will return `Ok(())` and `replay_on_archive`/`aptos-debugger`/`cli` replay verification will report success despite the state root divergence.

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
