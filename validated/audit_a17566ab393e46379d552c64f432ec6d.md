### Title
`ensure_match_transaction_info` skips checkpoint hash validation, allowing corrupted state roots to pass replay/output verification undetected - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-response/commit-verification routine used to confirm that a locally-produced (or externally-supplied) `TransactionOutput` matches an already-proven `TransactionInfo` leaf from the transaction accumulator. It checks status, gas used, write-set hash, and event-root hash, but a code comment explicitly documents that it **intentionally skips** comparing `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the recomputed values [1](#0-0) . This function backs `replay_on_archive`, the Aptos debugger, and CLI replay commands, meaning the very tools whose job is to catch state-commitment divergence cannot detect it in the state-checkpoint dimension.

### Finding Description
`ensure_match_transaction_info` validates a `TransactionOutput` against the corresponding proven `TransactionInfo`:
- transaction status vs. `txn_info.status()`
- `gas_used()` vs. `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs. `txn_info.state_change_hash()`
- event-root accumulator hash vs. `txn_info.event_root_hash()`

but never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed from the actual post-execution state [2](#0-1) . The trailing comment is explicit about the consequence: `replay_on_archive`-style tooling can report a "successful replay" even though the authenticated position/state root diverges from local execution, and instructs that the checkpoint hashes must be validated before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled [3](#0-2) .

`state_checkpoint_hash` (and its V1 siblings) is the field that binds a `TransactionInfo` leaf — and therefore the accumulator root committed to a `LedgerInfo` — to the Sparse-Merkle/Jellyfish state root actually produced by applying the write sets. This is exactly the "authenticated proof-bearing field" the task's Proof-and-Storage Pivots call out: `TransactionInfo` must "survive executor-to-storage handoff unchanged" and remain bound to the right root. By omitting the checkpoint-hash comparison, the one place that is supposed to catch mismatches between the write-set-derived events/status and the state root actually recorded on-chain fails to do so for the state root itself.

This routine is consumed by four call sites: `execution/executor/src/chunk_executor/mod.rs`, `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` [4](#0-3) . I was not able to fully inspect the `chunk_executor/mod.rs` call site within the effort budget of this pass, so I cannot confirm whether the live consensus/state-sync commit path has an independent, redundant checkpoint-hash check elsewhere in that pipeline (e.g., via `DoStateCheckpoint` recomputing and comparing the SMT root before commit) that would mask this gap in the normal validator commit path. What is locally provable from the code itself is that the replay/debug/CLI verification tools rely on this comparator as their integrity gate and that gate does not check the state-root-binding fields.

### Impact Explanation
This falls under "Hard-fork-only divergence during commit, replay, restore, or proof verification" from the State-Integrity Gate: a node (or auditor) replaying historical transactions, or verifying a chunk of outputs against an already-authenticated accumulator proof, can have its post-execution state secretly diverge from the canonical `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` while `ensure_match_transaction_info` reports success. Any bug in write-set application, state-checkpoint computation, or a future feature relying on `position_state_checkpoint_hash`/`hot_state_checkpoint_hash` (e.g., "trading-native" state roots referenced in the TODO) would go silently unverified by this authenticated check, undermining confidence in replay-verify results used to audit mainnet history integrity.

### Likelihood Explanation
The gap is unconditional (not behind a feature flag) and is exercised on every call to `ensure_match_transaction_info`, i.e., every replay/debug/CLI verification of transaction outputs against proven `TransactionInfo`. No privileged access is required to trigger it — anyone running `replay_on_archive`, the Aptos debugger, or affected CLI commands against archived data is relying on an incomplete check. The severity is bounded by the fact this is a verification-tool code path rather than the primary consensus commit path; I could not confirm within this pass whether the validator commit path has independent checkpoint-hash validation that would prevent actual bad state from being committed to mainnet, versus this only weakening after-the-fact auditing.

### Recommendation
Extend `ensure_match_transaction_info` to recompute the local state-checkpoint hash(es) (main, hot-state, and position-state where applicable) from the resulting `State`/`StateSummary` and assert equality against `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()`, mirroring the treatment already given to `state_change_hash` and `event_root_hash`. This should be done before any feature (e.g., `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on these fields is enabled, as the code's own TODO indicates.

### Proof of Concept
Not directly exploitable as a standalone PoC — the finding is a structural gap in an integrity-verification routine rather than an executable exploit. Demonstration would require: (1) constructing a `TransactionOutput` whose write set, when applied, yields a different Sparse Merkle root than the `state_checkpoint_hash` recorded in a legitimately-proven `TransactionInfo`, while keeping the write-set bytes (and thus `state_change_hash`) and events unchanged, and (2) showing `ensure_match_transaction_info` returns `Ok(())` for this mismatched pair, as it never reads `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` from `self` to compare [2](#0-1) .

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
