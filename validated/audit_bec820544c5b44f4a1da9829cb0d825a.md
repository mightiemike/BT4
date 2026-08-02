## Title
Replay/consistency check for `TransactionOutput` vs `TransactionInfo` silently ignores the position/hot-state checkpoint hashes, allowing a diverged trading-native state root to pass verification - ([File: types/src/transaction/mod.rs])

## Summary
`TransactionOutput::ensure_match_transaction_info` — the comparator used by replay/debugger tooling to confirm that a locally-recomputed `TransactionOutput` matches the authenticated `TransactionInfo` for a version — checks status, gas, write-set hash, and event root hash, but explicitly does **not** check the `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` fields of `TransactionInfoV1`. The code contains its own acknowledgement of this gap via an inline `TODO(trading-native)` comment.

## Finding Description
`ensure_match_transaction_info` is meant to assert that a re-executed/replayed `TransactionOutput` is faithful to the committed, consensus-authenticated `TransactionInfo` at a version. It validates:
- transaction status
- gas used
- write-set hash vs `txn_info.state_change_hash()`
- event root hash vs `txn_info.event_root_hash()` [1](#0-0) 

But right before returning `Ok(())`, the function comment states:

"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`." [2](#0-1) 

This is the same root-cause shape as the GTL bug: a value that is supposed to be part of the authenticated/committed accounting (there, GTL's `_subaccounts` set feeding `totalAssets`; here, the position/hot-state Merkle roots feeding `TransactionInfoV1`) is produced by one code path (execution's `DoStateCheckpoint::compute_position_checkpoint`, gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature) but is not actually cross-checked by the consumer path that is supposed to validate correctness (`ensure_match_transaction_info`, used by replay/debugger/db-tool). The feature-flag doc explicitly promises this root is "consensus-verified" once committed to `TransactionInfoV1`: [3](#0-2) 

but the one general-purpose comparator that tooling relies on to detect a divergence between local re-execution and the authenticated ledger record does not enforce that promise for the position (or hot-state) roots.

`ensure_match_transaction_info` is called from `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `storage/db-tool/src/replay_on_archive.rs`, and `aptos-move/cli/src/commands.rs` — i.e., it is the shared trust boundary these tools use to assert "my replayed execution matches the chain." A silent gap here means all of them can conclude a replay is correct while the position (trading-native) state tree — and consequently future proofs served against it — has silently diverged from what local execution actually produced.

## Impact Explanation
`compute_trading_native_state_roots` / native-position state is intended to become a consensus-verified part of `TransactionInfoV1` (per the feature flag documentation). If the position root diverges (e.g., due to a bug in `DoStateCheckpoint::compute_position_checkpoint`, `position_summary_at_commit`, or the underlying JMT extend/merklize logic) between two independent executions, `ensure_match_transaction_info` will still report success as long as write-set hash and event root match. This defeats the entire purpose of replay-verification/backup-restore-integrity tooling for this subsystem: a hard-fork-only or implementation-specific divergence in the position Merkle tree (which underlies authenticated proofs served for the trading-native state) would go undetected by the standard consistency check, meeting the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Authenticated API or state-view output bound to the wrong version, object, or proof context" criteria in the state-integrity gate.

## Likelihood Explanation
Currently `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is a new/likely-not-yet-enabled feature (permanent lifetime, but the code's own comment says to fix this "before enabling" it), so likelihood of exploitation today is low while the flag is off — with the flag off, `position_state_summary`/`position_state_checkpoint_hashes` are `None` and irrelevant. However, this is a real, currently-existing gap in the verification path, self-documented by the developers as a pre-condition that must be fixed before the feature is safe to turn on; any deployment enabling the flag without addressing this TODO inherits the gap immediately, and replay/debugger tools give false assurance of correctness for the position-state subsystem in the interim.

## Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, and `position_state_checkpoint_hash()` (when present on `txn_info` and available on the locally computed side) before returning `Ok(())`, matching the exact behavior of `DoStateCheckpoint::get_state_checkpoint_hashes`'s "known hashes" validation used at execution time. This closes the gap before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled in production, and ensures replay/backup/debugger tooling actually enforces the "authenticated position state root" invariant it is meant to.

## Proof of Concept
Not independently exploitable at present because `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is off by default and the position fields are `None` in that state — this finding is a code-level, self-acknowledged verification gap rather than a demonstrated on-mainnet state corruption today. A concrete PoC would require: (1) enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, (2) constructing a native-position write whose replay produces a different position Merkle root than the one committed to `TransactionInfoV1` (e.g., via a bug in `compute_position_checkpoint`'s handling of `last_inner_checkpoint_index` boundaries), and (3) showing `replay_on_archive` / `aptos-debugger` report success despite the mismatch. I could not execute this dynamically; the static evidence is the developers' own TODO comment describing exactly this scenario.

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
