### Title
`replay-verify` (`ensure_match_transaction_info`) never validates state-checkpoint/hot-state/position-state root hashes, allowing a state-computation divergence to pass replay verification undetected - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole correctness gate used by `db-tool replay-on-archive` (and `aptos-debugger`) to confirm that locally re-executed transactions match the authenticated, archived `TransactionInfo` for each version. The function checks status, gas, write-set hash (`state_change_hash`), and event root hash, but explicitly and knowingly skips comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — the fields that authenticate the actual Merkle root of world state at a checkpoint.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  performs four checks (status, gas_used, write_set_hash vs `state_change_hash`, event_root_hash) and then contains a TODO acknowledging the gap: [2](#0-1) 

This is the exact function invoked by the replay-verify tool per transaction to decide pass/fail: [3](#0-2) 

The state/hot-state/position-state checkpoint hashes are precisely the values that bind a `TransactionInfo` (and therefore the signed `LedgerInfo`/accumulator) to a specific Jellyfish-Merkle/position-state root — i.e., the proof-binding artifact for "what the state was" at that version, as seen in `assemble_transaction_infos`, which populates `maybe_state_checkpoint_hash`, `maybe_hot_state_checkpoint_hash`, and `maybe_position_state_checkpoint_hash` on every `TransactionInfoV1`: [4](#0-3) 

Because `ensure_match_transaction_info` never inspects these three fields, if the local re-execution during replay produces a *different* state root (e.g., due to a bug in state-checkpoint computation, the new `compute_trading_native_state_roots` code path, hot-state Merkle logic, or any regression that changes committed state without changing the write-set hash/events/status/gas — for example ordering, hashing, or eviction bugs in `HotState`/`State::update` seen in `storage/storage-interface/src/state_store/state.rs`), replay-verify will report success even though the locally computed and archived ledger states have diverged. This directly matches the "authenticated proof output bound to wrong version/root" and "hard-fork-only divergence during commit/replay" impact categories: the divergence is only observable by comparing state roots, and this is the one code path whose entire job is to make that comparison, yet it is disabled by omission.

### Impact Explanation
Replay-verify (`storage/db-tool/src/replay_on_archive.rs`) and the equivalent debugger path are the primary tools used to detect state-computation regressions against real chain history before/after upgrades (e.g., validating a new VM/execution change reproduces mainnet state exactly). A state-root divergence introduced by a bug (in the state store, hot-state layer, or the in-development trading-native/position-state-root feature flagged by `compute_trading_native_state_roots`) would silently pass this verification because the tool only checks write-set hash, not the resulting checkpoint state root. This can allow a consensus-breaking state-computation bug to ship undetected through the very process meant to catch it, which is squarely a "hard-fork-only divergence during commit/replay" integrity gap on authenticated proof data.

The impact is bounded by the fact that this does not directly corrupt mainnet's live, validator-signed ledger (validators still sign the real accumulator root during consensus), but it removes the safety net that is supposed to catch exactly this class of bug before it reaches mainnet, and the code's own TODO confirms this is a known, intentional gap tied to enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`.

### Likelihood Explanation
The gap is deterministic and always present — it is not a race condition or edge case; every call to `ensure_match_transaction_info` skips these fields. Its real-world trigger likelihood depends on there being an actual state-computation bug elsewhere (e.g., in `State::update`/`HotState` merge logic, or in a not-yet-fully-implemented feature such as trading-native state roots) that changes the checkpoint hash without changing `state_change_hash`/events/status/gas. Given the comment explicitly ties this to "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`," the codebase authors themselves flag this as a live pre-condition for an in-progress feature, indicating non-trivial likelihood once that feature activates.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `self`-derived checkpoint state root(s) against `txn_info.state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` whenever the transaction is a checkpoint, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, exactly as instructed by the existing TODO. This requires passing the computed state/hot-state/position-state root(s) for the checkpoint into this function (currently only `WriteSet`/events are compared) from the replay-verify and debugger call sites.

### Proof of Concept
Conceptual (cannot execute in this environment):
1. Introduce (or exploit an existing) hidden divergence in state root computation — e.g., a bug in `State::update`'s hot-state LRU/eviction logic in `storage/storage-interface/src/state_store/state.rs` (lines 192-323) that changes the final Merkle/hot-state root without altering any write-set entries, event list, gas used, or status.
2. Run `storage/db-tool/src/replay_on_archive.rs` against the archived chunk containing the affected checkpoint transaction.
3. `execute_and_verify` calls `ensure_match_transaction_info` (lines 392-397), which only compares status/gas/write_set_hash/event_root_hash — all unaffected by the bug — and returns `Ok(())`.
4. The tool reports the replay as fully verified/successful despite the locally computed state-checkpoint root (and hence the JMT/hot-state root that would be committed) diverging from the archived, validator-authenticated ledger state at that version — a silent hard-fork-class bug slipping past the one process meant to catch it.

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
