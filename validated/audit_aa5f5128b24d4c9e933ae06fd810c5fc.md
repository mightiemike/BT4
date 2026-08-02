### Title
`ensure_match_transaction_info` skips checkpoint-hash verification, letting `replay_on_archive` certify a divergent position/state root as valid - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function `db-tool`'s `replay_on_archive` verifier uses to confirm that locally re-executed transaction outputs match the archived, consensus-committed `TransactionInfo`. The function checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` on `TransactionInfoV1`. This means the tool that is meant to authenticate the committed state/proof roots against local re-execution can pass even when those roots diverge.

### Finding Description
`ensure_match_transaction_info` in `types/src/transaction/mod.rs` compares the locally computed `TransactionOutput` against the archived `TransactionInfo` on four axes only: execution status, gas used, write-set hash vs. `state_change_hash`, and event root hash. The function itself contains an explicit acknowledgment of the gap: [1](#0-0) 

That comment states the comparator "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution."

This is called from `storage/db-tool/src/replay_on_archive.rs`'s `execute_and_verify`, which is the sole mechanism used by the `replay-verify` tool to validate that re-executed outputs match the trusted, backed-up `TransactionInfo` for a version range: [2](#0-1) 

Because `TransactionInfoV1` (gated by `TRANSACTION_INFO_V1`) carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — all of which are Merkle roots that get folded into the transaction accumulator and hence the ledger's authenticated proof chain — omitting them from the replay comparator breaks the "committed state must match VM re-execution result" invariant for exactly the fields the on-chain accumulator proof authenticates: [3](#0-2) 

The `state_checkpoint_hash`/`position_state_checkpoint_hash` are produced by a separate merklization/commit pipeline (`storage/aptosdb/src/native_state_committer.rs`, `execution/executor/src/workflow/do_state_checkpoint.rs`) gated by the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature, and the code comments in that area independently flag this as an intentional pre-condition that must be closed "before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`": [4](#0-3) 

### Impact Explanation
If a validator, full node, or independent auditor ever produces a `position_state_checkpoint_hash` (or main/hot `state_checkpoint_hash`) during re-execution that differs from what is stored in the archived/backed-up `TransactionInfo` — due to a bug in the position-tree merklization path, an execution/consensus divergence, or corruption in the backup — `replay_on_archive` will not detect it and will report the replay as successful. This defeats the entire purpose of replay verification for these fields: a hard-fork-only state divergence in the native-position (trading) subsystem, or in the main/hot state checkpoint root, would go completely undetected by this tool, letting a corrupted or wrong ledger state pass as "verified" against the authenticated accumulator-backed `TransactionInfo`. This directly matches the "Committed state that differs from the correct VM result... accepted as valid" and "Authenticated API... output bound to the wrong version, object, or proof context" impact classes, since the comparator is the trust boundary between locally-computed state and the accumulator-authenticated `TransactionInfo`.

The severity is capped by the fact that `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `TRANSACTION_INFO_V1` are feature-gated and, based on the code and comments, not yet enabled/rolled out on mainnet; this is a real, admitted gap in the verification tooling rather than a live wrong-root-accepted-as-valid bug in current mainnet consensus, so it should be treated as high (not critical) until those features ship.

### Likelihood Explanation
The gap is 100% reproducible whenever `TransactionInfoV1` fields diverge from local re-execution — no attacker action or malicious peer is needed, only a divergence between the archived `TransactionInfo` and a locally executed `TransactionOutput`'s checkpoint hashes (e.g., a bug in the position-tree code, a corrupted backup, or a future on-chain feature enablement that introduces a hidden execution/state divergence). Because the code path is exercised on every call to `replay_on_archive`'s `execute_and_verify`, the missing check is deterministic and always in effect, not merely theoretical.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, and `txn_info.position_state_checkpoint_hash()` against the checkpoint hashes computed during local replay (the state, hot-state, and native-position roots, respectively) before returning `Ok(())`, and require this fix to land before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` or `HOT_STATE_ROOT_IN_TXN_INFO` is enabled on any network where `replay_on_archive`/replay-verify tooling is relied on for integrity assurance.

### Proof of Concept
1. Enable (or simulate, in a test harness) `TRANSACTION_INFO_V1` + `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, so `TransactionInfo::V1` carries a non-`None` `position_state_checkpoint_hash`.
2. Construct/backup a `TransactionInfo` whose `position_state_checkpoint_hash` is some fixed value `H1`.
3. Locally re-execute the same transaction such that the native-position write set produces a different Merkle root `H2 != H1` (e.g., by simulating a divergent merklization bug in `native_state_committer.rs`/`do_state_checkpoint.rs`, or by mutating a raw `TransactionInfoV1` fixture's `position_state_checkpoint_hash` field in a test before calling replay).
4. Call `TransactionOutput::ensure_match_transaction_info(version, &txn_info_with_H1, ...)` on the locally-produced output (which internally would correspond to `H2`) — observe that it returns `Ok(())` despite the checkpoint hash mismatch, because the function at [1](#0-0)  never inspects `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash`.
5. `replay_on_archive`'s `execute_and_verify` ( [5](#0-4) ) therefore records no error for this version, confirming that a replay-verify run over this range would incorrectly certify the divergent state root as matching the archive.

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

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

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

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
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
