### Title
Replay-verification comparator skips the state-checkpoint (state-root) hash, letting a divergent state root be reported as a verified replay - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by all offline/administrative replay-verification tooling (chunk-executor "verify execution" mode, `aptos-debugger`, the `cli` replay commands, and `db-tool`'s `replay_on_archive`) to confirm that a locally re-executed transaction matches the `TransactionInfo` that was actually committed to the chain. It checks status, gas, the write-set hash (`state_change_hash`) and the event root hash, but it explicitly never checks `state_checkpoint_hash` (nor `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`), which is the field that actually commits to the resulting state root (JMT root) after the transaction. This is called out in-code as a known, currently-unmitigated gap.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` performs four checks (status, gas, write-set hash, event root hash) and then returns `Ok(())` with a comment admitting the checkpoint hashes are not validated: [1](#0-0) 

The `state_checkpoint_hash` field on `TransactionInfo` is populated from the actual computed state-tree root for checkpoint transactions (built in `DoStateCheckpoint::run` / `Self::get_state_checkpoint_hashes` and wired into the `TransactionInfo` builder in `DoLedgerUpdate::assemble_transaction_infos`): [2](#0-1) [3](#0-2) 

This `TransactionInfo` (including `state_checkpoint_hash`) is exactly what is hashed into the transaction accumulator leaves and ultimately signed by validators in the `LedgerInfo` — i.e., it is the authenticated commitment to the post-transaction state root. However, when tooling reconstructs a `TransactionOutput` from local re-execution and wants to confirm it matches a *previously committed* `TransactionInfo` (from an archive, a backup, or a peer), it calls `ensure_match_transaction_info`, which silently omits comparing `state_checkpoint_hash` against the locally computed value. Callers of this function: [4](#0-3) 

are used from `aptos-debugger`, the CLI, and `storage/db-tool/src/replay_on_archive.rs` — the tools whose entire purpose is to detect state divergence caused by nondeterminism, storage bugs, or JMT computation errors between original execution and replay.

### Impact Explanation
Because `state_checkpoint_hash` is skipped, any bug that causes the recomputed state root to diverge from the originally committed one (e.g., a JMT/state-summary computation defect, a nondeterministic Move VM change, or corrupted/incorrectly-migrated ledger data) will not be caught by `replay_on_archive`, `aptos-debugger`, or the CLI replay-verification path — they will report the replay as matching even though the state root differs. This directly undermines a proof/commit integrity invariant this repo relies on for auditing hard-fork safety and detecting execution-determinism regressions before they reach mainnet, since these tools are the primary mechanism for catching exactly that class of bug prior to and after upgrades. The in-code TODO itself confirms the authors recognize this can mask "the authenticated position state root diverg[ing] from local execution" for the upcoming `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, but the gap is broader: even the baseline `state_checkpoint_hash` (unrelated to that unreleased feature) is unchecked today.

### Likelihood Explanation
This is not attacker-triggered in the traditional sense; it is a latent verification gap that will only manifest when there is a genuine state-root-affecting bug elsewhere in execution/storage. Its likelihood of "silently masking" such a bug is high precisely because it is the last line of defense (replay-verify) that operators/auditors rely on, and it currently provides false assurance for the one field (`state_checkpoint_hash`) that matters most for detecting state-root divergence.

### Recommendation
Extend `ensure_match_transaction_info` to also compare the locally computed state checkpoint hash(es) (`state_checkpoint_hash`, and — once relevant — `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) against `txn_info`'s values whenever the local state summary produced one, and fail replay verification on mismatch, rather than leaving this as a TODO gated on an unrelated feature flag.

### Proof of Concept
Not applicable as a live exploit — this is a static code-path proof: trace `chunk_executor::verify_execution` → `TransactionOutput::ensure_match_transaction_info` (`types/src/transaction/mod.rs:2139-2204`) shows no comparison against `txn_info.state_checkpoint_hash()`. Any test that produces a `TransactionOutput` with a correct write-set/events/gas/status but an artificially different state checkpoint hash (e.g., stub `DoStateCheckpoint` to return a mutated root) would pass `ensure_match_transaction_info` even though the state roots disagree, confirming the gap. [5](#0-4)

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

**File:** execution/executor/src/chunk_executor/mod.rs (L684-706)
```rust
        // not `zip_eq`, deliberately
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
```
