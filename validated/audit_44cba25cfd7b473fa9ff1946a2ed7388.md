## Finding: `replay-verify` and execution-verification tooling never validates the state checkpoint hash, so a corrupted state Merkle root passes as "verified" [1](#0-0) 

### Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash` / `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`, letting replay-verify and chunk-executor verification silently accept a wrong state root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole comparator used by every "did the replayed execution actually match the committed ledger" tool in the codebase: `storage/db-tool/src/replay_on_archive.rs` (the dedicated replay-verify security tool), `execution/executor/src/chunk_executor/mod.rs::verify_execution` (used by fast-sync/backup `replay_verify`/`verify` coordinators), `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`. It checks status, gas used, write-set hash, and event root hash, but deliberately skips the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` fields of `TransactionInfo` - a gap the code itself documents but does not fix.

### Finding Description
`ensure_match_transaction_info` compares a freshly produced `TransactionOutput` against a previously committed `TransactionInfo`: [2](#0-1) 

It validates `status`, `gas_used`, `write_set_hash` (hash of the raw `WriteSet` ops), and `event_root_hash`. It never touches `txn_info.state_checkpoint_hash()`, and the code contains an explicit acknowledgment of the gap: [3](#0-2) 

The `write_set_hash` only proves the *write operations* are byte-identical; it says nothing about whether applying those operations to the pre-state produces the same Jellyfish Merkle root. The state-checkpoint hash is the only field in `TransactionInfo` that authenticates the actual post-execution state root, and it is exactly the value that becomes a leaf's contribution to the transaction accumulator (and, transitively, to the signed `LedgerInfo`). By skipping it, this comparator cannot detect a state-root divergence introduced by a bug in JMT/state-tree update logic, hot-state computation, or any other post-write-set state-materialization step, as long as gas/status/events/write-set bytes still match.

This comparator is not a peripheral helper - it is the actual security check invoked by:
- `storage/db-tool/src/replay_on_archive.rs::execute_and_verify`, whose entire purpose is a from-scratch replay + verify against archived ledger data. [4](#0-3) 
- `execution/executor/src/chunk_executor/mod.rs::verify_execution`, used by backup/restore `replay_verify` and `verify` coordinators. [5](#0-4) 

### Impact Explanation
This is a proof/state-integrity gap in the tooling that is specifically relied upon to catch state divergence during restore/replay - exactly the "hard-fork-only divergence during commit, replay, restore, or proof verification" category. If any bug (e.g., in JMT node placement, hot-state checkpoint materialization, or the newer position-state checkpoint path) causes the locally recomputed state root to diverge from the archived/committed one while the write set, gas, status, and events remain identical, `replay-verify` and equivalent verification flows will report success. Operators, auditors, and automated CI relying on these tools to catch consensus/state divergence bugs before a hard fork or during backup validation would get a false "all good," allowing a corrupted or forked ledger state to go undetected until it manifests elsewhere (e.g., a validator halting on an actual state root mismatch in production, at which point the tooling that should have caught it earlier had already signed off).

### Likelihood Explanation
The gap is deterministic and always present - it is not conditional on attacker behavior; any state-materialization bug that leaves write-set bytes unchanged (a very plausible bug class, since state root computation is a separate stage from producing the write set) will slip past every caller of `ensure_match_transaction_info` today. The bug is explicitly called out by the maintainers' own TODO comment referencing the `position_state_checkpoint_hash` (native-trading) feature, confirming this is a known, currently-unaddressed gap rather than a purely theoretical one.

### Recommendation
Extend `ensure_match_transaction_info` to also assert `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` against the locally recomputed values whenever those hashes are present (i.e., when the executed output includes a state checkpoint), mirroring the same "compute value, compare to `txn_info`, `ensure!`" pattern already used for `write_set_hash` and `event_root_hash`. This requires threading the freshly computed state checkpoint hash into `TransactionOutput` (or exposing it alongside the `TransactionOutput` at call sites) so it is available to compare in all four call sites, before enabling any features (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depend on this comparator being trustworthy.

### Proof of Concept
Not directly exploitable by an unprivileged attacker against mainnet consensus (the state root is still authenticated end-to-end via validator signatures over the `LedgerInfo`/accumulator in the normal commit path). The concrete, provable defect is a logic gap in the verification tool itself: construct a `TransactionOutput` with the same `write_set`, `gas_used`, `status`, and `events` as a stored `TransactionInfo`, but ensure the previously-computed `state_checkpoint_hash` in that `TransactionInfo` does not correspond to the actual post-state root (e.g., by manually building a `TransactionInfo` with an arbitrary `state_checkpoint_hash`, as unit tests already do via `TransactionInfo::builder_v0()...maybe_state_checkpoint_hash(...)`, then calling `ensure_match_transaction_info`). The call returns `Ok(())`, demonstrating that any state-root divergence is invisible to this check.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2203)
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

**File:** execution/executor/src/chunk_executor/mod.rs (L692-705)
```rust
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
```
