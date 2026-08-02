### Title
`ensure_match_transaction_info` fails to validate state-checkpoint / position-checkpoint hashes during chunk-replay verification, letting a diverged authenticated state root pass as "verified" - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that `ChunkExecutorInner::verify_execution` calls (from `remove_and_replay_epoch`, used by the backup-restore replay path and `db-tool`'s `replay_on_archive`) to confirm that locally re-executing a transaction produces the *same* committed result as the authenticated `TransactionInfo` pulled from a backup/archive. It checks status, `gas_used`, write-set hash (`state_change_hash`), and event root hash, but it explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that carry the Sparse-Merkle-Tree/native-position state roots. This gap is called out in the code itself as a known TODO.

### Finding Description
`ensure_match_transaction_info` in [1](#0-0)  compares four things between the locally-produced `TransactionOutput` and the trusted/authenticated `TransactionInfo`: execution status, gas used, write-set hash, and event root hash. It stops there, with an explicit comment: [2](#0-1) 

This comment states plainly that the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", and that `replay-verify` tooling can therefore "report a successful replay even when the authenticated position state root diverges from local execution."

This function is invoked from `ChunkExecutorInner::verify_execution`, which is the verification step used during epoch replay (`remove_and_replay_epoch` / `TransactionReplayer`), consumed by the backup restore path (`storage/backup/backup-cli/src/backup_types/transaction/restore.rs`) and by `db-tool replay-on-archive`: [3](#0-2) 

Because `state_checkpoint_hash` (the JMT/SMT world-state root at a checkpoint boundary) and `position_state_checkpoint_hash` (the native-position state root, gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature) are never compared here, a locally-computed state root that differs from the trusted, previously-committed root at the same version will not be flagged as a mismatch by this check. The actual root-hash comparison for these checkpoints only happens inside `DoStateCheckpoint::get_state_checkpoint_hashes`, gated behind the `known_state_checkpoints`/`known_position_state_checkpoints` parameters supplied via the *block-based* `update_ledger` path: [4](#0-3) 

However, that check is only reached through `ChunkExecutorInner::update_ledger` (state-sync/chunk apply flow) — the replay-verify path exercised via `verify_execution`/`ensure_match_transaction_info` bypasses it entirely for the purpose of "verify_execution_mode.should_verify()" — meaning the specific replay-verification codepath that is supposed to be an independent correctness oracle for state roots silently omits checking them.

### Impact Explanation
This breaks the "authenticated API / proof-bearing response must stay bound to the right ledger version and root" invariant for the replay-verification tooling: an executor that re-derives a different (wrong) state or position-state root for a given version — due to a VM/state-computation bug, a non-deterministic bug, or corrupted intermediate state — will be reported as passing verification by `verify_execution`, because the only fields it authenticates are status/gas/write-set-hash/event-root, not the checkpoint root hashes. This directly undermines the replay-verify safety net that release engineering and node operators rely on to catch hard-fork-causing divergences before they reach production, and it also means CLI/db-tool replay comparisons (`aptos-move/cli/src/commands.rs` uses the same comparator) can mask a genuine state-root divergence as a clean replay. Because the JMT root and native-position root are exactly the values used for proof verification and API responses bound to a ledger version, silently accepting a diverged root is a proof-integrity gap, not merely cosmetic.

### Likelihood Explanation
Likelihood of the underlying state divergence occurring is separate from likelihood of this masking bug being triggered: whenever any state/position root divergence does occur (e.g., a subtle non-determinism, an unrelated storage bug, or version-skew during replay), this comparator will fail to surface it. Since the code comment itself documents this as a known limitation ("Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`"), the gap is real and acknowledged, but its downstream severity is contingent on some other, separate root-divergence trigger occurring; this function does not itself cause the divergence, it fails to detect one that already happened elsewhere.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled) `position_state_checkpoint_hash` against the locally computed equivalents before enabling any feature that depends on native-position state roots, exactly as the existing TODO comment recommends.

### Proof of Concept
A concrete PoC cannot be constructed purely from static analysis: this is a detection/verification gap, not itself a state-corruption bug. To demonstrate impact, one would need to (1) engineer or replay a transaction whose local re-execution produces a different `state_checkpoint_hash`/`position_state_checkpoint_hash` from the trusted `TransactionInfo` at the same version (e.g., via a deliberately introduced non-deterministic write), and (2) show that `ChunkExecutorInner::verify_execution` / `db-tool replay_on_archive` reports success despite this divergence, because `ensure_match_transaction_info` never inspects those fields (as shown at [5](#0-4) ). I was not able to fully verify how `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is currently gated in production (whether it's active on mainnet) within the available indexed context, so the practical exploitability/activation status of the position-root portion of this gap remains uncertain and should be checked directly in `types/src/on_chain_config/aptos_features.rs`.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-707)
```rust
        for (version, txn_out, txn_info, write_set, events) in multizip((
            begin_version..end_version,
            &execution_output.to_commit.transaction_outputs,
            transaction_infos.iter(),
            write_sets.iter(),
            event_vecs.iter(),
        )) {
            if let Err(err) = txn_out.ensure_match_transaction_info(
                version,
                txn_info,
                Some(write_set),
                Some(events),
            ) {
                return if verify_execution_mode.is_lazy_quit() {
                    error!("(Not quitting right away.) {}", err);
                    verify_execution_mode.mark_seen_error();
                    Ok(version + 1)
                } else {
                    Err(err)
                };
            }
        }
        Ok(end_version)
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
