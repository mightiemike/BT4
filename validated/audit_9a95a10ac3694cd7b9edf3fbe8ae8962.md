## Title
`ensure_match_transaction_info` skips validation of state/hot-state/position checkpoint hashes, letting replay-verify accept a corrupted or diverging state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-consistency check used by replay/verification tooling (`storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, `execution/executor/src/chunk_executor/mod.rs`) to confirm that a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed/signed on-chain. The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but its own inline comment admits it never checks `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or the newly introduced `position_state_checkpoint_hash`.

### Finding Description [1](#0-0) 

The function computes and compares:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (hash of `self.write_set()`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It never recomputes or compares against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` — the fields that actually commit to the Sparse-Merkle/Jellyfish state root, the hot-state root, and (in this fork) the new native "position" state root introduced for `COMPUTE_TRADING_NATIVE_STATE_ROOTS`. The comment directly above the `Ok(())` return states this explicitly:

```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

This matters because these checkpoint hashes are precisely the values whose correctness depends on state-commitment logic elsewhere in the fork (e.g. `DoStateCheckpoint::run` / `compute_position_checkpoint` in `execution/executor/src/workflow/do_state_checkpoint.rs`, which builds `LedgerWithSummary<PositionStateWithSummary>` and derives `position_state_checkpoint_hash` from a `HashMap`-collapsed set of native "position" writes). If that new position-state computation has any determinism/aggregation bug (e.g. write-set ordering, key collisions, or an inconsistent parent/persisted base as seeded in `compute_position_checkpoint`), `ensure_match_transaction_info` provides no signal — it will still return `Ok(())` even though the recomputed state root does not match the one embedded in the on-chain `TransactionInfo`.

`ensure_match_transaction_info` is called from state-integrity-critical tooling:
- `storage/db-tool/src/replay_on_archive.rs` — used to independently re-verify historical execution against archived on-chain data.
- `execution/executor/src/chunk_executor/mod.rs` — used during chunk-based state sync / restore to validate re-executed transactions against the ledger's committed `TransactionInfo`s.
- `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` — used for transaction re-execution/debugging against mainnet data.

In all of these call sites, the function is the single line of defense that is supposed to catch "committed state differs from correct VM result." Skipping the state/hot-state/position checkpoint hash checks means silent state-root divergence (whether from a genuine executor bug, data corruption, or a malicious archive/full node feeding crafted `TransactionInfo`s during chunk sync) is not detected.

### Impact Explanation
This breaks the "committed state must not diverge silently" and "proof-bearing responses must stay bound to the right ledger version/root" invariants called out in the State-Integrity Gate. Concretely:
- During chunk-executor sync (`execution/executor/src/chunk_executor/mod.rs`), a node ingesting transactions/outputs and their claimed `TransactionInfo`s could persist a state whose Merkle/hot-state/position root does not actually match what was executed, and `ensure_match_transaction_info` would not catch it, since it never checks `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` equality.
- `replay_on_archive` (used for independent, off-chain re-verification of historical ledger correctness — a primary defense against hard-fork-only or executor divergence bugs) would report success even when the state root differs, defeating its entire purpose for exactly the class of bug (state-commitment divergence) it exists to catch.

This is a high-severity, state-integrity-relevant gap: it doesn't itself corrupt data, but it silently disables the verification mechanism that is supposed to catch state divergence in the state-checkpoint/position-state code paths, meaning any bug in `DoStateCheckpoint`'s position/hot-state computation (a large, non-trivial, newly-added piece of logic in this fork) can persist to mainnet storage undetected by this tool.

### Likelihood Explanation
The gap is unconditional (not behind a feature flag check in `ensure_match_transaction_info` itself) and is exercised on every call from the listed call sites. The only reason impact is currently bounded is that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` may not yet be enabled in production, per the comment ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS") — but the state and hot-state checkpoint hash checks are missing regardless of that flag, meaning even ordinary state-checkpoint divergence (unrelated to the new position-state feature) is unverified by this function today.

### Recommendation
In `ensure_match_transaction_info`, recompute the expected `state_checkpoint_hash` (and, when applicable, `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`) from the locally-produced state summary at that version and assert equality with `txn_info`'s corresponding fields, mirroring the existing `write_set_hash` / `event_root_hash` checks, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or relying on `replay_on_archive`/chunk-executor verification for state-root integrity.

### Proof of Concept
Not applicable as an exploit PoC — the vulnerability is a verification-gap issue provable purely from local code inspection: [1](#0-0)  shows the comparator omits checkpoint-hash checks that are essential to detecting state-commitment divergence, self-documented in the surrounding TODO comment.

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
