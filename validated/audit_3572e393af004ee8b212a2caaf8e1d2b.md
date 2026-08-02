### Title
`ensure_match_transaction_info` Skips Checkpoint-Hash Validation, Allowing Replay/Verify to Accept a Divergent Position/State Root - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by chunk replay/verify and debugger tooling to confirm that a locally re-executed `TransactionOutput` matches an authenticated `TransactionInfo` at a given version. The function validates status, gas, `write_set` hash (`state_change_hash`), and `event_root_hash`, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap acknowledged in-code by a `TODO(trading-native)` comment.

### Finding Description [1](#0-0) 

The function computes and compares only:
- transaction status vs `txn_info.status()`
- `gas_used()` vs `txn_info.gas_used()`
- `CryptoHash::hash(self.write_set())` vs `txn_info.state_change_hash()`
- event-root hash vs `txn_info.event_root_hash()`

It never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed from local re-execution. The trailing comment states this directly:

> "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS."

This is called by `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs` — i.e., the code paths used by replay-verify (`storage/db-tool/src/replay_on_archive.rs`) and by chunk execution/verification flows, which are the actual mechanisms that are supposed to detect divergence between locally computed state and what was authenticated on mainnet by `TransactionInfoV1`.

The new `TransactionInfoV1.position_state_checkpoint_hash` and `hot_state_checkpoint_hash` fields (guarded by `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` features) are committed to the accumulator and thus consensus-verified [2](#0-1) , but `ensure_match_transaction_info` cannot catch a bug that produces the wrong position/hot-state root during re-execution.

### Impact Explanation
If a bug exists in `DoStateCheckpoint::compute_position_checkpoint` (or the hot-state root computation) that computes an incorrect `position_state_checkpoint_hash`/`hot_state_checkpoint_hash` relative to what was actually committed on-chain, `ensure_match_transaction_info` will still report a match, masking the divergence. This defeats the primary safety net (replay-verify / chunk verification) that is meant to catch state-computation regressions before/after these trading-native features are enabled on mainnet, since the check silently ignores exactly the fields these new features add. This is a genuine gap in the proof/commit integrity of validation tooling, not merely a cosmetic issue.

### Likelihood Explanation
This is currently **low-to-moderate likelihood** to be independently exploitable/triggered because:
- The gap is self-documented as a known TODO ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"), suggesting the feature (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, `HOT_STATE_ROOT_IN_TXN_INFO`) may not yet be enabled on mainnet.
- I was unable to fully confirm from the index whether these features are currently live in mainnet governance config, or whether an independent divergence-detection mechanism exists elsewhere (e.g., a stricter check inside `DoLedgerUpdate` or `ChunkResultVerifier` in `execution/executor/src/chunk_executor/chunk_result_verifier.rs`, which I could not fully inspect due to iteration limits).
- The bug itself does not directly corrupt committed state — it weakens the *detection* mechanism (replay-verify/chunk-verify), so its severity depends on whether a separate, independent computation bug in the checkpoint root logic exists to actually diverge state.

Given the required "State-Integrity Gate" (accepting only impacts where committed/verified state can differ from correct VM result, or an authenticated proof output can be wrongly accepted), this finding qualifies as a genuine local gap: an authenticated field (`position_state_checkpoint_hash`/`hot_state_checkpoint_hash`) can silently diverge from local re-execution without being caught by the very function meant to catch it. However, I could not verify from the available code alone whether an actual root-computation bug exists to trigger a real-world divergence — only that the verification check is incomplete.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally computed values (when available/applicable given feature flags), as the existing TODO comment already recommends, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` are enabled in production.

### Proof of Concept
Not directly demonstrable via a standalone PoC snippet from static analysis alone — the gap is structural: call `ensure_match_transaction_info` with a `TransactionOutput`/re-executed state whose `position_state_checkpoint_hash` differs from `txn_info.position_state_checkpoint_hash()`; the function returns `Ok(())` regardless, since the corresponding `ensure!` check for those fields is absent (confirmed by reading the full body of the function, ending at `Ok(())` with no such comparisons) [3](#0-2) . Full confirmation of end-to-end triggerability (i.e., whether an independent root-computation bug currently produces a real mismatch) would require deeper inspection of `DoStateCheckpoint::compute_position_checkpoint` and `ChunkResultVerifier`, which I was not able to complete within the available tool budget.

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-209)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```
