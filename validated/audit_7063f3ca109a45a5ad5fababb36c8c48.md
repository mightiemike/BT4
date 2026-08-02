### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint/hot-state/position root comparison, letting replay-verify accept a divergent committed state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the authenticated-equality check used to confirm a locally re-executed `TransactionOutput` matches the `TransactionInfo` that was actually committed to the ledger accumulator. It checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the very fields that bind a transaction to the Sparse-Merkle/JMT state root. This is analogous to the reported Vader bug: a comparison omits the field that anchors the "amount"/"root" to the correct context, allowing divergent state to be silently accepted as valid.

### Finding Description [1](#0-0) 

```rust
pub fn ensure_match_transaction_info(...) -> Result<()> {
    ...
    // checks status, gas_used, write_set_hash vs state_change_hash, event_root_hash
    ...
    // TODO(trading-native): this comparator ignores the checkpoint hashes
    // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
    // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
    // replay even when the authenticated position state root diverges from
    // local execution. Validate the checkpoint hashes here before enabling
    // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
    Ok(())
}
```
The code itself documents the gap. `TransactionInfoV1` carries `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` [2](#0-1) , all of which are consensus-verified roots bound into the transaction-info leaf that feeds the ledger accumulator. `ensure_match_transaction_info` is called from `storage/db-tool/src/replay_on_archive.rs` and `execution/executor/src/chunk_executor/mod.rs` (confirmed by `grep_search`, though the exact call sites could not be re-read before the tool budget ran out) as the correctness gate for transaction replay/verification tooling. Because the checkpoint-hash fields are skipped, a replay that produces a different state root (main state, hot state, or the native-position tree gated by `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) than what is committed on-chain would still pass this check.

### Impact Explanation
This breaks the "committed state that differs from the correct VM result...accepted as valid" and "authenticated API or state-view output bound to the wrong version/root" invariants from the State-Integrity Gate. Replay-verify and db-tool tooling built on `ensure_match_transaction_info` (used to detect divergence between archived, potentially attacker/relay-supplied, transaction outputs/write-sets and the locally re-executed result) would not catch a case where the write set matches (same `state_change_hash`) but the resulting Merkle/JMT root differs — e.g., due to a state-key encoding bug, a stale/incorrect base for the `position` (trading-native) state tree, or corrupted persisted-state input. This is exactly the class of bug the code comment itself flags as dangerous "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS." A silent root divergence in verification tooling means archive-based restore/replay validation can rubber-stamp a corrupted or hard-forked state root as consistent, undermining the guarantee that authenticated proof/root fields are checked end-to-end.

### Likelihood Explanation
The gap is deterministic and doesn't require a malicious actor to trigger — any code path in which the write set hash matches but a state-checkpoint root differs (bugs in state-key encoding for the newly added `TradingNative`/`Position` key variant, incorrect base persisted-state summary threading through `ProvablePositionStateSummary`, or partial rollout mismatches of `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` feature flags) will pass undetected through this specific verification function. The comment in the code confirms the maintainers are aware and consider it unsafe to enable the trading-native root feature until fixed, which corroborates the exploitability/impact assessment.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present/known) between the recomputed `TransactionInfo` and the `expected` one, gated appropriately by whichever feature flags determine if those fields are populated, before this comparator is relied upon by any replay/restore verification path — and certainly before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled in production.

### Proof of Concept
Not independently constructible from the available index snippets: I could not fully view the exact call sites in `storage/db-tool/src/replay_on_archive.rs` and `execution/executor/src/chunk_executor/mod.rs` (the file reads failed due to tool-call errors and the iteration budget was exhausted), so I cannot confirm whether an additional, independent state-root check exists elsewhere in those call paths that would compensate for this gap. The vulnerability claim rests on the explicit in-code TODO acknowledging the missing checks in `ensure_match_transaction_info` at `types/src/transaction/mod.rs:2197-2202`; a Devin session with full file access should verify the call sites to confirm no redundant check exists before treating this as fully confirmed.

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
