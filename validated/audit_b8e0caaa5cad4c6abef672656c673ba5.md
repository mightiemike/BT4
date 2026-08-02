## Finding: `TransactionOutput::ensure_match_transaction_info` skips validation of the native-position (and hot-state) checkpoint hash, allowing replay-verification to accept a divergent authenticated state root

### Summary
The comparator used to validate that a re-executed `TransactionOutput` matches its committed `TransactionInfo` (`ensure_match_transaction_info`) checks status, gas, write-set hash, and event root hash — but explicitly does **not** check `position_state_checkpoint_hash` (or the hot-state checkpoint hash) against the locally recomputed value. This is acknowledged in-line by the code's own TODO comment. [1](#0-0) 

### Finding Description
`TransactionOutput::ensure_match_transaction_info` is the function relied upon by replay/verification tooling (`db-tool`'s `replay_on_archive`, `aptos-debugger`, and the executor's chunk replay path) to assert that locally recomputed execution output is consistent with the `TransactionInfo` that was actually committed to the ledger accumulator. It validates `status`, `gas_used`, the write-set hash against `state_change_hash`, and the event root hash — but the trailing comment states plainly:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." [2](#0-1) 

This is the exact bug-class analog to the external report's root issue: a proof/state-selection routine returns "success" (an early/incomplete accept) without checking a value (`B`/native-position root) that could differ from the correct/expected one. Here, the "commitment gate" (`ensure_match_transaction_info`) is the analog of the operator-selection function that early-returns without considering all relevant state — it green-lights a replay as matching even though a whole authenticated root component (`position_state_checkpoint_hash`, part of `TransactionInfoV1`, feeding the ledger accumulator via `TransactionInfo::hash()`) is left unchecked.

The native-position state root is designed to be consensus-verified: per the feature-flag documentation, when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, "execution computes the trading-native state roots and commits them to `TransactionInfoV1`, so they are consensus-verified." [3](#0-2) 

The checkpoint hash is produced during `compute_position_checkpoint` in `DoStateCheckpoint::run`, and it is only optionally cross-checked against `known_position_state_checkpoints` inside the executor's live/streaming pipeline (`Self::get_state_checkpoint_hashes` in `do_state_checkpoint.rs`) — that check is separate from, and not exercised by, `ensure_match_transaction_info`, which is the function specifically used by *offline replay/verification tools*. [4](#0-3) [5](#0-4) 

### Impact Explanation
If/when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is turned on (it is documented as "Lifetime: permanent", i.e., intended to ship), any tool that relies on `ensure_match_transaction_info` for replay-verification (`db-tool replay_on_archive`, `aptos-debugger`) will report a **successful replay** even if the locally recomputed native-position Merkle root diverges from the one actually committed to the chain's `TransactionInfoV1`. This defeats the entire purpose of replay verification for that state tree: a node operator or auditor could believe historical execution is verified/consistent while the position (trading-native) ledger state is silently corrupted or manipulated relative to the authenticated on-chain root. This is a proof/commitment-integrity gap in the "authenticated API and proof-bearing responses must stay bound to the right ledger version, root, and object" category, since replay tooling is exactly the mechanism meant to bind local re-computation to the authenticated `TransactionInfo` root.

### Likelihood Explanation
Likelihood is currently **low/contingent**, because:
- `ENABLE_TRADING_NATIVE` is hardcoded to `false` in this snapshot, so the native-position subsystem is not attached to `AptosDB` and no `position_state_checkpoint_hash` is populated on mainnet today. [6](#0-5) 
- The gap is only reachable once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled via governance, at which point the comparator gap becomes live and every replay-verification run silently ignores position-root divergence.
- The bug is already self-documented as a TODO by the code's own authors, indicating it is known, tracked, and expected to be fixed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`" — this reduces novelty but does not eliminate the risk if the flag is enabled before the fix lands.

### Recommendation
Before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, extend `ensure_match_transaction_info` to also verify `txn_info.position_state_checkpoint_hash()` (and the hot-state checkpoint hash) against the locally recomputed position/hot-state checkpoint root, mirroring the write-set/event checks already present, so that replay/verification tooling cannot report success while an authenticated state root diverges.

### Proof of Concept
Not applicable as an exploitable PoC in this snapshot: the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) is not enabled and `ENABLE_TRADING_NATIVE` is `false`, so `position_state_checkpoint_hash` is always `None` in committed `TransactionInfo` today, meaning the comparator gap has no live effect on mainnet as currently configured. The finding is a code-level proof that the check is missing (per the code's own TODO), not a demonstrated present-day exploit — this should be tracked and remediated as a blocking prerequisite before the feature flag is turned on, per the recommendation above.

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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-955)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L86-99)
```rust
    /// Computes the position summary (latest + last_checkpoint) and per-txn
    /// position root for this chunk by extending the parent on the persisted
    /// base. The root depends only on the position writes, not on the base, so
    /// it's deterministic across nodes.
    fn compute_position_checkpoint(
        execution_output: &ExecutionOutput,
        parent: Option<&LedgerWithSummary<PositionStateWithSummary>>,
        persisted: &ProvablePositionStateSummary,
        known_position_state_checkpoints: Option<Vec<Option<HashValue>>>,
    ) -> Result<(
        LedgerWithSummary<PositionStateWithSummary>,
        Vec<Option<HashValue>>,
    )> {
        let _timer = OTHER_TIMERS.timer_with(&["get_position_checkpoint_hashes"]);
```

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L178-185)
```rust

        // Per-tx hash vector + known-hash validation (shared with main/hot state).
        let hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_position_state_checkpoints,
            new_last_checkpoint.root_hash(),
            "position_state",
        )?;
```

**File:** storage/aptosdb/src/trading_native.rs (L1-12)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

//! Crate-wide flag for the native-trading subsystem (positions today;
//! orders and collateral land later). Gates whether
//! `AptosDB::open_internal` attaches the position DBs, runs the
//! native commit applier, and exposes the in-memory mirror.

/// Flip to `true` once order/collateral land.
pub(crate) const ENABLE_TRADING_NATIVE: bool = false;


```
