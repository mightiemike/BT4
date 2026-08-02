### Title
`ensure_match_transaction_info` never validates `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`, allowing a divergent committed checkpoint root to pass replay verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling (`aptos-debugger`, `aptos-move/cli`, and `storage/db-tool/src/replay_on_archive.rs`) to check that a locally re-executed `TransactionOutput` matches the authenticated `TransactionInfo` fetched from a trusted source (archive/backup, ledger). It checks status, gas, write-set hash, and event-root hash, but it explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or the newly-added `position_state_checkpoint_hash` fields, as flagged by its own inline `TODO(trading-native)` comment.

### Finding Description [1](#0-0) 

The function computes and compares `write_set_hash` and `event_root_hash` against the `TransactionInfo`, but the state-checkpoint-related roots are never derived from local execution state and compared to the corresponding fields on `txn_info`:
- `txn_info.state_checkpoint_hash()`
- `txn_info.hot_state_checkpoint_hash()`
- `txn_info.position_state_checkpoint_hash()`

This is acknowledged directly in the source: [2](#0-1) 

These checkpoint hashes are exactly the fields that bind a `TransactionInfo` (and therefore the transaction accumulator leaf/root that is authenticated by a `LedgerInfo`) to the *actual* state committed in storage — analogous to `state_checkpoint_hash` for main state, and to the new `position_state_checkpoint_hash` for the custom native-position (trading) state added in this repo, which is threaded through the checkpoint/ledger-update pipeline here: [3](#0-2) 
and computed here: [4](#0-3) 

Since `ensure_match_transaction_info` is the trust boundary used by `replay_on_archive` and other replay-verify tooling to assert "this `TransactionOutput` I just re-executed matches the trusted/authenticated `TransactionInfo`", the omission of the checkpoint-hash comparisons means: if local re-execution or the position/hot state pipeline diverges from what is actually authenticated in the accumulator (e.g. due to a bug in `compute_position_checkpoint`'s parent/persisted-base seeding logic, a state-sync/restore ordering issue, or corruption of the position Merkle tree), the mismatch is silently swallowed by this comparator. The tool reports "replay verified OK" while the authenticated state root (main, hot, or position) that is actually bound into the accumulator differs from what local execution/storage produced.

### Impact Explanation
This breaks the "committed state must match VM result" and "authenticated root must be checked against local computation" integrity guarantee for any tooling built on `ensure_match_transaction_info` (`db-tool replay-on-archive`, `aptos-debugger`, `aptos-move/cli`). A divergence in the state checkpoint root (particularly the new position-state root, whose commit path is quite involved — parent/persisted-base chaining across chunks, in-memory pipeline seeding, hot/position pruning) would go undetected by the one tool whose job is specifically to catch such divergences during archive replay/verification. This directly enables "committed state that differs from the correct VM result" to escape detection at the state-integrity layer, satisfying the required-impact bar of "Wrong ... state proof accepted as valid" (here: accepted as *matching*, when in fact it does not).

### Likelihood Explanation
The bug is not gated behind privileged or adversarial preconditions — it is a straightforward comparator function missing required checks, guaranteed to be silent 100% of the time regardless of the actual state, since the code path for those three fields simply does not exist. Given this repo also introduces `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (a non-trivial new subsystem with cross-chunk state chaining), any latent divergence bug in that new pipeline would be masked precisely by this gap, increasing the practical likelihood of an undetected fork/replay divergence going unnoticed until it manifests as a mainnet consensus/replay disagreement.

### Recommendation
Extend `ensure_match_transaction_info` to also compute and compare, when the checkpoint is expected at this version:
- the local state-checkpoint root vs `txn_info.state_checkpoint_hash()`
- the local hot-state checkpoint root vs `txn_info.hot_state_checkpoint_hash()` (when hot-state-root-in-txn-info is enabled)
- the local position-state checkpoint root vs `txn_info.position_state_checkpoint_hash()` (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled)

This closes the gap flagged by the existing `TODO(trading-native)` comment before that feature is enabled in production.

### Proof of Concept
Not directly exploitable as a standalone PoC without a live divergence in the checkpoint-computation pipeline (e.g., a bug in `DoStateCheckpoint::compute_position_checkpoint`'s parent/persisted seeding, or a corrupted position Merkle DB after restore). The demonstrable, code-level proof is the comparator itself:
1. Construct a `TransactionOutput` whose write set/events match the `TransactionInfo`, but whose backing state (after real execution) would produce a different `state_checkpoint_hash`/`position_state_checkpoint_hash` than the one stored in `txn_info`.
2. Call `ensure_match_transaction_info(version, txn_info, ...)`.
3. Observe it returns `Ok(())` despite the checkpoint-root mismatch, because the function body at lines 2139–2204 never reads or compares those three fields — confirmed by direct inspection of the function and its own TODO note.

Note: I was not able to fully trace an end-to-end trigger for an actual on-chain root divergence in the position-state pipeline (e.g., in `compute_position_checkpoint`'s parent/persisted-base chaining) within the available investigation; that remains a plausible but unconfirmed root cause for producing the initial divergence that this comparator would then fail to catch. The confirmed, code-proven vulnerability is specifically the missing checkpoint-hash validation in `ensure_match_transaction_info`.

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L62-83)
```rust
        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };

        Ok(StateCheckpointOutput::builder()
            .state_summary(state_summary)
            .state_checkpoint_hashes(state_checkpoint_hashes)
            .maybe_hot_state_checkpoint_hashes(hot_state_checkpoint_hashes)
            .maybe_position_state_summary(position_state_summary)
            .maybe_position_state_checkpoint_hashes(position_state_checkpoint_hashes)
            .build())
```
