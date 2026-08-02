## Title
Replay-verify integrity check silently ignores state-checkpoint root mismatches - (File: `types/src/transaction/mod.rs`)

## Summary
`TransactionOutput::ensure_match_transaction_info` — the routine used by the replay-verify tooling (`storage/db-tool/src/replay_on_archive.rs`) and other debugging/verification tools to confirm that a freshly re-executed transaction matches the authenticated `TransactionInfo` pulled from an archive/backup — checks status, gas, write-set hash, and event-root hash, but never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash`. This is the same rounding/short-cut precision-loss bug class as the M-10 report (a value that is supposed to gate a critical comparison is computed/consumed in a way that silently drops precision/coverage), except here the "precision" dropped is an entire authenticated field of the committed ledger state.

## Finding Description
`TransactionInfo` is the struct whose hash is stored in the transaction accumulator and is what ledger-info signatures ultimately authenticate [1](#0-0) . It carries `state_checkpoint_hash` (and in V1, `hot_state_checkpoint_hash` / `position_state_checkpoint_hash`) representing the authenticated Sparse-Merkle-Tree state root at that version [2](#0-1) .

`ensure_match_transaction_info` is the function that is supposed to prove "the locally computed `TransactionOutput` matches the trusted, already-authenticated `TransactionInfo`." It validates status, gas, write-set hash, and event root hash, but the function returns `Ok(())` immediately after event-hash verification, and a comment explicitly documents that the state/hot-state/position checkpoint hashes are skipped: [3](#0-2) 

This function is invoked by `replay_on_archive.rs`'s `execute_and_verify`, which is the tool operators use to confirm that replaying history against an archived backup reproduces the exact same ledger state: [4](#0-3) 

Because the state-checkpoint hash comparison is missing, any divergence between the locally recomputed state root (from `do_state_checkpoint`/JMT batch update) and the state root that was actually authenticated on-chain in `TransactionInfo` goes completely undetected by this check. `execute_and_verify` will report the chunk as verified even though the state root diverged.

## Impact Explanation
This breaks the "proof-binding" invariant required by the State-Integrity Gate: an authenticated, version-bound root (the state-checkpoint hash embedded in the accumulator-proven `TransactionInfo`) is treated as verified when it was never actually checked. Concretely, this masks:
- Hard-fork-only divergence during replay/restore — precisely one of the explicitly in-scope impacts — where a bug in state-checkpoint/JMT root computation (e.g., a resource-group merge bug, an aggregator materialization bug, or a hot-state/position-state root bug) would cause silent, undetected divergence between the archived-authenticated ledger state and what replay tooling asserts is correct.
- False confidence for operators/auditors who rely on `replay_on_archive`, `aptos-debugger`, and the `aptos-move/cli` uses of this same function to certify that historical ledger state is reproducible and correct, when in fact a corrupted state root would pass silently.

Because state root corruption combined with a verification tool that cannot detect it is exactly the kind of "wrong accumulator root / proof accepted as valid" scenario called out in the Required Impacts, and because a state-root computation bug is by nature a consensus/hard-fork-class bug once triggered on real execution, the severity is high: the very safety net designed to catch such state-integrity regressions has a documented blind spot.

## Likelihood Explanation
This is not a hypothetical or speculative scenario — the code's own comment states outright that this exact failure mode is possible ("replay-verify tooling ... can report a successful replay even when the authenticated position state root diverges from local execution"). No privileged access, malicious peer, or governance action is required to trigger the underlying condition; any bug elsewhere in state-checkpoint computation (which is a large, actively-evolving surface, e.g., hot-state / "trading-native" position roots) would be silently swallowed by this specific verification gap the moment it's exercised through the shared `ensure_match_transaction_info` call path used by `replay_on_archive.rs`, `aptos-debugger`, and `aptos-move/cli`.

## Recommendation
Extend `ensure_match_transaction_info` to compute the locally observed state-checkpoint hash(es) (state, hot-state, and position-state checkpoint hashes when present on the `TransactionOutput`/execution context) and `ensure!` they equal `txn_info.state_checkpoint_hash()` / `hot_state_checkpoint_hash()` / `position_state_checkpoint_hash()`, mirroring the existing write-set-hash and event-root-hash checks, before enabling any feature (e.g. `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that depends on this tooling providing accurate assurance.

## Proof of Concept
1. In a test/replay harness, construct a `TransactionOutput` whose write set, events, gas, and status match a given `TransactionInfo`, but whose corresponding execution state-checkpoint root (as would be produced by `do_state_checkpoint`) differs from `txn_info.state_checkpoint_hash()` (simulate this by mutating a single leaf in the SMT used to derive the checkpoint hash after write-set application, independent of the write set itself — e.g. a hot-state or position-state divergence).
2. Call `output.ensure_match_transaction_info(version, &txn_info, Some(&write_set), Some(&events))`.
3. Observe the call returns `Ok(())` at line 2203 of `types/src/transaction/mod.rs`, i.e., verification succeeds despite the state root mismatch, demonstrating the exact scenario the inline comment warns about. [3](#0-2)

### Citations

**File:** types/src/transaction/mod.rs (L2196-2204)
```rust

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** types/src/transaction/mod.rs (L2229-2236)
```rust
/// `TransactionInfo` is the object we store in the transaction accumulator. It consists of the
/// transaction as well as the execution result of this transaction.
#[derive(Clone, CryptoHasher, BCSCryptoHash, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub enum TransactionInfo {
    V0(TransactionInfoV0),
    V1(TransactionInfoV1),
}
```

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L392-397)
```rust
            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
```
