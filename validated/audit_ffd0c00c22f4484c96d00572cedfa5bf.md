### Title
`ensure_match_transaction_info` silently skips verification of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, allowing a diverged authenticated state root to pass replay/chunk verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used to confirm that a locally re-executed `TransactionOutput` (write set, events, status, gas) matches an already-committed, proof-carrying `TransactionInfo`. Its own inline comment admits it intentionally omits comparing the checkpoint-hash fields — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — before returning `Ok(())`. [1](#0-0) 

### Finding Description
`ensure_match_transaction_info` checks transaction status, gas used, write-set hash (`state_change_hash`), and event root hash against the target `TransactionInfo`, but explicitly does **not** validate the state-checkpoint-related hashes: [2](#0-1) 

These checkpoint hashes are exactly the fields that bind a `TransactionInfo` (and therefore the accumulator leaf / accumulator root it feeds into) to a specific *state root* — main state (`state_checkpoint_hash`), hot-state root (`hot_state_checkpoint_hash`), and the custom native-position Merkle root (`position_state_checkpoint_hash`) introduced in this fork. These roots are computed in `DoStateCheckpoint` and threaded through `assemble_transaction_infos` in `execution/executor/src/workflow/do_ledger_update.rs`, and are the values that later get hashed into `TransactionInfo` and therefore into the ledger's transaction accumulator: [3](#0-2) 

`ensure_match_transaction_info` is used by `execution/executor/src/chunk_executor/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` as the sanity check that a replayed/re-executed transaction produces the state the archive/chain claims. Because the checkpoint-hash fields are skipped, none of these call sites can detect a divergence purely from this comparator: if local re-execution computes a different state-checkpoint hash, hot-state root, or position-state root than what was actually committed/proof-carried in the `TransactionInfo`, the mismatch is not raised as an error here.

This is a direct analog of the PoolTogether bug pattern: an integrity check that is supposed to gate acceptance of state (the "new observation should be recorded/verified") is bypassed under a specific condition (here, unconditionally for these three fields), letting an incorrect/corrupted piece of committed ledger state (the state root bound into `TransactionInfo`/accumulator) be treated as valid by downstream consumers that rely on this function as their correctness oracle.

The severity is amplified because the code comment itself states the exact consequence: "so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution." This confirms in-repo that `replay_on_archive` — a tool whose entire purpose is to assert that historical, cryptographically-anchored (accumulator/ledger-info-signed) state matches recomputation — can report false success while the position-state root (and, by the same code path, the main state-checkpoint hash and hot-state root) has silently diverged.

### Impact Explanation
This falls squarely within the "Proof And Storage Pivots" scope: "Authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" and "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged." A verification routine that is supposed to detect a wrong state-checkpoint root (main state, hot state, or native position state) instead unconditionally accepts it. Practical fallout:
- `replay_on_archive` / the CLI / `aptos-debugger` replay tooling can report a clean, "verified" replay even though the recomputed state-checkpoint/hot-state/position-state root diverges from the historically committed one — defeating the entire point of replay verification and hiding state-commitment bugs (including hard-fork-only divergences) from operators and auditors.
- Because `compute_trading_native_state_roots` is a newly-introduced feature in this fork (native position Merkle root wired into `TransactionInfoV1`), any bug in that new state-computation path (e.g., in `DoStateCheckpoint::get_position_checkpoint_hashes`) would go undetected by this verification gate specifically, even though the gate exists for that exact purpose.

### Likelihood Explanation
The gap is unconditional and always present whenever `ensure_match_transaction_info` is invoked — it is not gated by a rare timing window like the PoolTogether bug, it is a permanent skip. Triggering the *underlying* state-root divergence still requires a bug elsewhere in state-root computation (e.g., in the native-position or hot-state root logic, which is explicitly flagged as new/unfinished by comments such as "TODO(HotState): this is currently None in testnet and mainnet"), but once such a divergence exists — for any reason, including a future bug in the new `position_state`/`hot_state` code — this check will not catch it. The in-repo TODO explicitly acknowledges the gap is real and unresolved, which is strong internal proof of the missing invariant, though the current blast radius is bounded by whether the roots being skipped are relied upon (mainnet doesn't yet run hot-state or position roots per comments), so today's practical severity is mainly on replay-verification confidence rather than full consensus-critical acceptance.

### Recommendation
In `ensure_match_transaction_info` (`types/src/transaction/mod.rs`), before returning `Ok(())`, add explicit comparisons between the locally-derivable checkpoint hashes and `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` whenever those fields are expected to be present (i.e., wire the `expected` state/hot-state/position roots through to this function the same way `expected_write_set`/`expected_events` are already threaded in), and `ensure!` they match, returning a descriptive error otherwise. Until this is done, `replay_on_archive` and any `VerifyExecutionMode::Verify` path should not be treated as verifying state-checkpoint or native-position root correctness.

### Proof of Concept
1. Enable `compute_trading_native_state_roots` and have a block whose native-position state root computation is buggy or is fed corrupted (but self-consistent) writes, so the locally computed `position_state_checkpoint_hash` at `DoStateCheckpoint::run` differs from what was actually committed/expected for that version.
2. Run `storage/db-tool/src/replay_on_archive.rs`'s replay-verify flow, which calls `TransactionOutput::ensure_match_transaction_info` to confirm the replayed transaction matches the archived `TransactionInfo`.
3. Because `ensure_match_transaction_info` never compares `position_state_checkpoint_hash` (nor `state_checkpoint_hash`/`hot_state_checkpoint_hash`), the check returns `Ok(())` and the tool reports a successful, verified replay, even though the state root diverges — exactly as documented by the TODO at [4](#0-3) .

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
