Based on my investigation, I found a concrete, self-documented state-integrity gap in the transaction-output verification routine used by Aptos's replay/verify tooling, rather than a supportable analog of the "AND-instead-of-OR authorization" bug pattern (no evidence of a functionally-equivalent double-restriction bug was found in the write-set/proof/commit paths I examined).

### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, allowing replay-verify to certify a diverged state root - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info`, the routine used by db-tool's replay/verify tooling to confirm that locally re-executed output matches the authenticated, on-chain `TransactionInfo`, intentionally omits comparison of `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`. This means state-root divergences between local execution and the archived, signed ledger are not detected by this check.

### Finding Description
`ensure_match_transaction_info` compares status, gas used, write-set hash (`state_change_hash`), and event root hash against the target `TransactionInfo`, but explicitly does not compare the checkpoint hash fields: [1](#0-0) [2](#0-1) 

The code's own comment acknowledges the gap: *"this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."*

This function is consumed by `aptos-move/cli/src/commands.rs` and, transitively, by db-tool's `replay_verify` / `replay_on_archive` subcommands (`storage/db-tool/src/lib.rs`), whose entire purpose is to catch exactly this class of divergence — differences between locally-recomputed state (JMT/state-checkpoint root, hot-state root, or the newer native-position state root) and the value actually committed/signed at that version. Because the checkpoint-hash fields are excluded from the equality check, a state-root divergence (e.g., caused by an executor bug, a schema/versioning bug, or a hard fork) at a state-checkpoint transaction would pass `ensure_match_transaction_info` silently.

### Impact Explanation
`TransactionInfo.state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` are the authenticated Merkle roots binding a given ledger version to the correct account/resource/hot-state/position state — the primary state-commitment invariant covered by the "Proof And Storage Pivots" in scope. A replay/verify tool whose comparator ignores these fields cannot fulfil its stated integrity role: it can certify "successful replay" for a version whose real state diverged from the network's committed and signed state, masking a hard-fork-class corruption instead of surfacing it. Given that replay-verify is the primary safety net operators/auditors rely on to detect exactly this kind of divergence (post-hoc, across upgrades or executor changes), a false-negative here is high severity for ledger integrity assurance, even though it does not directly corrupt a live validator's own committed state.

### Likelihood Explanation
This is not a hypothetical edge case — it is present in the shipped comparator used by the shipped `replay_verify`/`replay_on_archive` tools, and is explicitly acknowledged as an unaddressed gap in the code (guarded behind a not-yet-enabled `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag referenced in the same comment). Any state-root divergence occurring at a checkpoint transaction during a replay job will reliably go undetected until the flag/validation is added.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against the locally recomputed values (when both sides have them), gating this either behind the same flag mentioned in the TODO or making it unconditional before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, so replay-verify tooling cannot report success when the authenticated state root diverges from local execution.

### Proof of Concept
Not independently reproducible as a live/consensus-path exploit — the gap is confined to the offline `ensure_match_transaction_info` comparator called from `aptos-move/cli/src/commands.rs` / `storage/db-tool` replay tooling. Concretely: construct a `TransactionOutput` whose write set/events/status/gas match the target `TransactionInfo` (so those checks pass) but whose resulting state-checkpoint/hot-state/position-state root would differ (e.g. corrupt one unrelated state item and produce a matching write-set hash coincidentally, or, more directly, run the existing `test_verify_transaction`-style harness and observe that `ensure_match_transaction_info` returns `Ok(())` while `state_checkpoint_hash` values differ) — this returns `Ok(())` since the checkpoint-hash fields are never inspected, confirmed directly by the code and its own comment at [2](#0-1) .

**Caveat on confidence:** I could not fully trace whether the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature flag referenced in the comment already gates callers to avoid this gap in practice, nor confirm all current call sites of `ensure_match_transaction_info` beyond `aptos-move/cli/src/commands.rs` (index coverage may be incomplete for that file, which returned only its header line). This finding affects replay/verification tooling integrity, not a validator's live consensus/commit path — I flag this distinction explicitly since it affects how the "mainnet impact" criterion should be weighed.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2178)
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
```

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
