## Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting replay/restore report success despite a diverging state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/backup verification tooling to confirm that locally re-executed transaction output matches the authenticated `TransactionInfo` fetched from an already-verified source (archive/backup/accumulator proof). It checks `status`, `gas_used`, `write_set_hash` (`state_change_hash`), and `event_root_hash`, but explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the locally computed values.

### Finding Description
The function is defined at [1](#0-0) . It validates four of the fields carried in `TransactionInfo` (status, gas, write-set hash, event root hash) but ends with an explicit acknowledgement that the state-checkpoint-related hashes are never checked: [2](#0-1) 

These skipped fields — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — are exactly the fields that bind a `TransactionInfo` (and therefore the authenticated transaction accumulator leaf, and ultimately the `LedgerInfo`/accumulator root that consensus signs) to the Sparse-Merkle-Tree state root, hot-state root, and position-state root computed by `DoStateCheckpoint`/`DoLedgerUpdate` (see the analogous field assembly in `assemble_transaction_infos`, [3](#0-2) ).

This comparator is called from `verify_execution` in the chunk executor, which is the code path used to validate re-executed transactions against `transaction_infos` supplied from an already-verified chunk (e.g. during backup/state-sync/replay verification): [4](#0-3) , as well as from `aptos-debugger` and the Move CLI replay tooling (both call `ensure_match_transaction_info`, per `grep` results in `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs`).

Because these three fields are silently excluded from the equality check, if a locally re-executed transaction produces a *different* state/hot-state/position-state root than what the authenticated `TransactionInfo` (and, transitively, the accumulator root signed by validators) commits to — due to a state-computation bug, non-determinism, or divergent logic on the replaying/restoring node — `ensure_match_transaction_info` will still return `Ok(())`. The write set and event hashes matching is not sufficient, since the state checkpoint hash is a separate, additional binding of the "authenticated ledger state" to the accumulator; a divergence there is a distinct, unauthenticated-vs-authenticated mismatch that this function's contract is specifically meant to catch and does not.

### Impact Explanation
This breaks the "authenticated proof-bearing responses/replay outputs must stay bound to the right ledger version, root, and object" invariant: replay-verify and restore-verification tooling built on `ensure_match_transaction_info` can declare success even though the locally derived state root (or hot-state/position-state root) diverges from the version actually committed and signed on mainnet. Any hard-fork-only or environment-dependent state-computation divergence (e.g., a bug specific to the "trading-native"/position-state feature, referenced directly by the code's own TODO mentioning `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) would go undetected by this verification path, undermining confidence in replay-verify/backup-restore correctness guarantees and potentially masking a state-corrupting divergence from operators who rely on this check as their integrity gate.

### Likelihood Explanation
This is not an attacker-triggered exploit in the classic sense — the gap is a code-level, always-present logic omission rather than a privileged-input condition, and it is already flagged in-repo by the authors as a known/`TODO` gap ("this comparator ignores the checkpoint hashes ... so replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"). It matters because it is live, unprivileged code on the mainnet replay/restore/debugger paths right now, gated only by a future feature flag comment rather than by actual enforcement in the current implementation.

### Recommendation
Extend `ensure_match_transaction_info` to also compute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the locally re-executed output) against the corresponding fields on `txn_info`, returning an error on mismatch, consistent with how `write_set_hash` and `event_root_hash` are already validated.

### Proof of Concept
Not applicable / not independently demonstrated with a concrete divergent execution — this finding is based on direct code inspection of the comparator's implementation and its call sites, plus the author's own acknowledgment comment. No test harness was run to trigger an actual local/authenticated state-root divergence, and I could not fully trace every call site (`aptos-move/cli/src/commands.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`) end-to-end within the available iterations to confirm they don't perform their own separate checkpoint-hash comparison elsewhere in the same flow; this should be verified against the full call context before treating the gap as unmitigated in every caller.

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

**File:** execution/executor/src/chunk_executor/mod.rs (L685-706)
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
```
