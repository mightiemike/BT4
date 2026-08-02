## Analysis Summary

Reducing the external report to its core invariant: **proof-bearing / authenticated ledger data must be validated against locally-recomputed integrity fields; if the checker silently skips a subset of fields, corruption in those fields cannot be detected.**

I generated and evaluated several candidate paths in write-set conversion, `TransactionInfo` assembly, storage-restore, and accumulator/proof verification. Most verification paths (`AccumulatorProof::verify`, `SparseMerkleProof::verify_by_hash_partial`, `JellyfishMerkleRestore::verify_chunk`, `TransactionInfoListWithProof::verify_extends_ledger`) correctly fold every field into the checked hash and reject mismatches. The one candidate with a **self-documented, provable integrity gap** is `TransactionOutput::ensure_match_transaction_info`, which is the function used by replay/verification tooling (`db-tool`'s `replay_on_archive`, `aptos-debugger`, `aptos-move/cli`) to confirm that locally re-executed output matches the authenticated/committed `TransactionInfo`.

### Title
Replay-verification comparator silently skips state/hot-state/position checkpoint hashes, allowing undetected corruption of committed state roots - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the sole invariant check used by all replay-verification tooling to confirm that a locally re-executed transaction produced the same result as the one already committed/archived on-chain. The function checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that represent the authenticated Merkle roots of committed ledger state.

### Finding Description [1](#0-0) 

`ensure_match_transaction_info` asserts equality on:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (`CryptoHash::hash(self.write_set())`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

but the function ends with only a comment and `Ok(())`:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
``` [2](#0-1) 

`state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` are precisely the authenticated Merkle roots (state tree, hot-state tree, and the new "position state" tree gated by `compute_trading_native_state_roots`) that are assembled per-transaction in `assemble_transaction_infos` and folded into the `TransactionInfo` hash that ultimately sits in the transaction accumulator: [3](#0-2) . These are exactly the fields that the "Proof And Storage Pivots" call out as needing deterministic proof binding across executor-to-storage handoff and replay.

This function is called by the real replay/debug entry points:
- `storage/db-tool/src/replay_on_archive.rs`
- `aptos-move/aptos-debugger/src/aptos_debugger.rs`
- `aptos-move/cli/src/commands.rs`

These tools compare locally re-executed `TransactionOutput`s against transaction infos pulled from an archive/full node, and are the primary defense used by Aptos Labs and third parties to detect non-deterministic execution or storage bugs (including hard-fork-causing divergences) before/after upgrades. Because the comparator omits the checkpoint-hash fields, any bug that corrupts the checkpoint-hash computation path (Jellyfish Merkle root calc, hot-state root calc, or the new position-state root calc) — while leaving write-set/event hashes intact — will pass replay verification with `Ok(())`, giving false assurance that execution is correct.

### Impact Explanation
This is a proof/replay-verification-integrity break, not consensus-visible corruption by itself, but it is a hard-fork-detection gate defect: it makes the state-commitment consistency oracle blind to exactly the class of divergence (state root / hot-state root / position-state root mismatch) that would otherwise indicate a consensus split or storage corruption bug. Per the stated scope, "Hard-fork-only divergence during commit, replay, restore, or proof verification" is explicitly in-scope, and an authenticated proof-context field ("bound to the wrong version/root") not being checked at all during replay directly satisfies "Authenticated API or state-view output bound to the wrong version, object, or proof context." Any real bug elsewhere in state-checkpoint-hash computation becomes silently undetectable by this tooling, which undermines the guarantee that "committed state differs from correct VM result... must be caught."

### Likelihood Explanation
The gap is unconditional and always present for every `TransactionInfoV1` (i.e., for any deployment using the V1 transaction-info format, which includes hot-state and position-state checkpoint hashes) — no attacker action or special ordering is needed; the comparator is simply missing the assertions, as acknowledged by the inline TODO. The only mitigating factor is that this is a detection-tool gap rather than a live-consensus bug, so its "impact" materializes only in combination with some other bug in checkpoint-hash computation; on its own it doesn't corrupt the ledger.

### Recommendation
Extend `ensure_match_transaction_info` to also assert equality of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (via `txn_info.state_checkpoint_hash()` etc., compared against the locally computed values passed in or recomputed from `self`), before any code path (including `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) relies on this function to certify replayed execution.

### Proof of Concept
1. In a debug/dev build, execute a transaction whose write set and events are identical to the on-chain committed transaction, but whose `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) is deliberately mutated/corrupted before calling `ensure_match_transaction_info` (e.g., patch `TransactionInfo::builder_v1()...maybe_state_checkpoint_hash(wrong_hash)` in a test harness).
2. Call `ensure_match_transaction_info(version, corrupted_txn_info, None, None)`.
3. Observe the function returns `Ok(())` despite the state-checkpoint hash mismatch, because none of the `ensure!` checks reference `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`.
4. This demonstrates that `db-tool`'s `replay_on_archive`, `aptos-debugger`, and `cli` replay-verify flows built on this function cannot detect divergence in these authenticated roots.

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
