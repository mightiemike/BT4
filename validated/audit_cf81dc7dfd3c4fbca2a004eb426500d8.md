## Title
`TransactionOutput::ensure_match_transaction_info` skips verifying state-checkpoint / hot-state / position-state root hashes against the authenticated `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used to check that a locally re-executed (or replayed) `TransactionOutput` matches an authenticated `TransactionInfo` (the leaf committed into the transaction accumulator and covered by a `LedgerInfo` signature / proof). It validates status, gas used, write-set hash, and event-root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` fields carried in `TransactionInfo`, per an inline `TODO(trading-native)` comment.

### Finding Description [1](#0-0) 

The function's doc/inline comment states plainly:
```
// TODO(trading-native): this comparator ignores the checkpoint hashes
// (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
// tooling (e.g. db-tool's `replay_on_archive`) can report a successful
// replay even when the authenticated position state root diverges from
// local execution. Validate the checkpoint hashes here before enabling
// COMPUTE_TRADING_NATIVE_STATE_ROOTS.
```
i.e., the code itself documents that this check is incomplete: it compares `status`, `gas_used`, `write_set_hash` (against `state_change_hash`), and `event_root_hash`, but returns `Ok(())` unconditionally without ever comparing `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` to any locally computed equivalents.

This mirrors the bug-class from the external report: a validation routine that is supposed to gate acceptance of a state transition/commitment silently omits a required field from its check, so a value that should be tied to the check (here, the checkpoint/state root hash embedded in the authenticated `TransactionInfo`) is effectively treated as unconstrained ("bypassed"), just as `share == 0` bypassed the `allowedBorrow` check in the original report.

`TransactionInfo` (V0/V1) carries `state_checkpoint_hash`, and V1 additionally carries `hot_state_checkpoint_hash` and `position_state_checkpoint_hash` — these are exactly the checkpoint roots produced by `DoStateCheckpoint`/`DoLedgerUpdate` and are part of what gets hashed into the transaction-info leaf and ultimately the accumulator root that `LedgerInfoWithSignatures` attests to: [2](#0-1) 

Because `ensure_match_transaction_info` is the comparator used by replay/verification tooling (explicitly called out as "db-tool's `replay_on_archive`"), a divergence between the locally-recomputed state (specifically anything touching the "trading-native"/hot-state/position state trees) and the state root embedded in an authenticated `TransactionInfo` will not be detected by this check. The write set and event hashes are checked, but the state (and hot-state/position-state) *checkpoint* roots — which represent the committed Merkle-tree root of the entire ledger state after the transaction, distinct from the transaction's own write set — are not.

### Impact Explanation
This breaks the "proof and storage pivot" invariant that authenticated state roots (accumulator/checkpoint hashes) bound into `TransactionInfo`/`LedgerInfo` must be independently re-derivable and must match local execution. If the actual (buggy or malicious) executor/storage state-checkpoint computation for the "trading-native" state tree (hot state / position state) diverges from what is embedded in the signed `TransactionInfo`, `ensure_match_transaction_info` will still return `Ok(())`. This means:
- Replay-verification tooling used for auditing archived history (`db-tool replay-verify` / `replay_on_archive`) can incorrectly certify that a chain segment is "correct" even when the position/hot-state root has silently diverged from the authenticated commitment.
- Any downstream code relying on this comparator as the sole state-integrity gate for replay would fail to catch state corruption or a hard-fork-class divergence specifically in the hot-state/position-state Merkle roots.

This matches the required "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "Wrong ... state proof accepted as valid" impact categories.

### Likelihood Explanation
The gap is deterministic and always present (not merely triggerable by an attacker input) — it's a structural omission in the verification function, documented in the code itself via the TODO. It is guarded from being a live security incident today because the "trading-native"/hot-state feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`) that would make this divergence exploitable/observable is apparently not yet enabled by default (per the TODO wording "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"). This substantially lowers current-mainnet likelihood, but the check is still incomplete in code as it exists, and any caller depending on `ensure_match_transaction_info` for full state-checkpoint validation (not just write-set/event validation) is silently under-protected today.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` from `txn_info` against locally computed equivalents (when available/applicable), consistent with how `write_set_hash` and `event_root_hash` are already checked, before this comparator is relied upon for any state root that includes those trees.

### Proof of Concept
Not independently exploitable as a standalone PoC without enabling the not-yet-active `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/hot-state feature path, since that is what makes the omitted fields carry real state. Conceptually: construct a `TransactionOutput` whose recomputed hot-state/position-state root differs from the `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` fields of a given (otherwise-valid, correctly-signed) `TransactionInfo`, while keeping `write_set`, `events`, `gas_used`, and `status` identical to what `txn_info` expects. Calling `ensure_match_transaction_info` on this pair will return `Ok(())`, incorrectly certifying that the two match. I could not fully verify end-to-end triggerability given the feature appears gated off in this codebase state; this is noted as an open uncertainty.

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

**File:** execution/executor/src/workflow/do_ledger_update.rs (L95-122)
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
                let txn_info_hash = txn_info.hash();
```
