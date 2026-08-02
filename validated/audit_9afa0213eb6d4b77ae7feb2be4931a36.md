### Title
`ensure_match_transaction_info` silently skips the position/hot-state checkpoint hashes, letting `db-tool replay-verify` / `replay_on_archive` accept a corrupted authenticated ledger state - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function replay-verification tooling (`storage/db-tool/src/replay_on_archive.rs`) uses to check that a freshly re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed and accumulator-proven on chain. The function explicitly does **not** compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — this is called out in a `TODO` comment in the code itself.

### Finding Description
`ensure_match_transaction_info` compares status, gas used, write-set hash (`state_change_hash`), and event root hash against the target `TransactionInfo`, but the comment at [1](#0-0)  states plainly that the comparator ignores the state/hot-state checkpoint hashes and the `position_state_checkpoint_hash`. This means `replay-verify`/`replay_on_archive` (`storage/db-tool/src/replay_on_archive.rs`, `Verifier::execute_and_verify`/`verify`) can report a transaction as successfully replayed even though the locally computed Sparse-Merkle-Tree checkpoint root (or hot-state / position state root) diverges from the one embedded in the historical `TransactionInfo` and bound into the accumulator via `assemble_transaction_infos` [2](#0-1) .

Because `TransactionInfoV1` also carries `position_state_checkpoint_hash` as a "repurposed reserved field" [3](#0-2) , any bug in native-position/hot-state computation that produces a wrong checkpoint root would not be caught by replay-verify, since that specific field is the one explicitly excluded from comparison.

### Impact Explanation
This breaks the "Committed state that differs from the correct VM result" and "authenticated API / state-view output bound to the wrong version/root" integrity gates: the officially sanctioned verification tool used to certify archive/backup integrity and catch consensus/state divergence would pass a chunk of history even if the state (or hot-state / trading-native position) root diverges from what execution actually produces. This undermines confidence in `replay-verify` results used to validate backups and detect forks/bugs, and could mask genuine state corruption in the native-position subsystem (gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) from replay-based detection.

### Likelihood Explanation
Moderate. The gap is triggered whenever `replay_on_archive`/`replay-verify` is run against data with non-trivial write-sets and any of the checkpoint-hash-producing subsystems (state checkpoint, hot state, or native-position/trading state) has a defect. It requires no special privilege to trigger — it's purely a gap in the local verification logic reachable by anyone running the official replay tooling, and the maintainers' own comment confirms the gap is real and un-mitigated as of this code (referencing `COMPUTE_TRADING_NATIVE_STATE_ROOTS` as the feature that would need the fix before being enabled).

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash` (when present), and `position_state_checkpoint_hash` (when `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` is enabled), matching them against the locally recomputed checkpoint roots before treating a replayed chunk as valid, and add a regression test that intentionally corrupts a checkpoint hash to confirm replay-verify now fails.

### Proof of Concept
I could not fully verify an end-to-end triggering path within the available iterations (e.g., confirming that `replay_on_archive`'s call sites never separately re-check the checkpoint hash elsewhere, and confirming feature-flag interaction with `COMPUTE_TRADING_NATIVE_STATE_ROOTS` gating). The maintainers' own TODO comment is direct evidence of the gap, but I was not able to trace whether any other code path (e.g., inside `execute_and_verify` in `replay_on_archive.rs`, whose body I could not fully read due to large omitted regions in the file) independently re-derives and compares the checkpoint hash, which would mitigate the issue. Given this residual uncertainty, this should be treated as a confirmed logic gap with unconfirmed full exploitability rather than a fully proven end-to-end PoC.

### Citations

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
```

**File:** types/src/transaction/mod.rs (L2452-2461)
```rust
    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
}
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L82-110)
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
```
