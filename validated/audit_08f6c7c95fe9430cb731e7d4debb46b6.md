## Title
`ensure_match_transaction_info` skips checkpoint-hash validation, allowing replay/state-sync verification to accept a divergent state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info()` in [1](#0-0)  is the authenticated cross-check used by replay/verification tooling and the chunk executor to confirm that a locally re-executed (or state-synced) `TransactionOutput` matches the `TransactionInfo` bound into the transaction accumulator/ledger. It validates status, gas used, write-set hash (`state_change_hash`) and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that summarize the Sparse-Merkle state root at checkpoint boundaries. This is the same class of bug as the referenced `minievm` finding: a verification/consistency-check function silently accepts data it should have rejected because it fails to validate a specific field, letting divergent underlying state pass as "verified."

### Finding Description
The comment directly above the `Ok(())` return admits the gap: [2](#0-1) 

`ensure_match_transaction_info` checks:
- transaction status [3](#0-2) 
- gas used [4](#0-3) 
- write-set hash vs. `state_change_hash` [5](#0-4) 
- event root hash [6](#0-5) 

It never compares `txn_info.state_checkpoint_hash()`, `txn_info.hot_state_checkpoint_hash()`, or `txn_info.position_state_checkpoint_hash()` against anything computed locally, even though `TransactionInfoV1` carries these fields specifically to authenticate the Sparse Merkle Tree / hot-state / native-trading-position state roots at checkpoint boundaries [7](#0-6) . `ensure_match_transaction_info` is called from `execution/executor/src/chunk_executor/mod.rs` and from replay tooling (`aptos-debugger`, CLI commands) to decide whether replayed/synced execution "matches" the on-chain/backup record.

### Impact Explanation
Because the function is the single consistency gate between a locally computed `TransactionOutput` and the accumulator-committed `TransactionInfo`, omitting the checkpoint-hash comparisons means: if the actual state root computed during replay, chunk execution, or backup restore diverges from the authenticated `state_checkpoint_hash`/`position_state_checkpoint_hash` recorded on-chain (e.g., due to a state-computation bug, a non-deterministic native-trading-position root, or corrupted restore data), this divergence is not detected. Tooling such as `db-tool replay-on-archive` (and any chunk-executor path relying on this check) will report a successful, "verified" replay/backup even though the durable state (SMT root / hot-state root / position state root) is wrong. This is exactly the proof-integrity invariant the Gate calls out: "Wrong accumulator root, Merkle proof, transaction proof ... accepted as valid," applied here to the state-checkpoint root rather than the accumulator root.

### Likelihood Explanation
This is a real, currently-shipped gap and not merely theoretical: it is self-documented as a `TODO(trading-native)` in the code, meaning the state-checkpoint verification is deliberately incomplete pending the `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature. However, I could not fully verify from the indexed code whether `state_checkpoint_hash` is validated elsewhere in the call paths that use `ensure_match_transaction_info` (e.g., whether the chunk executor's `StateSyncChunkVerifier`/`ReplayChunkVerifier` perform an equivalent checkpoint-hash check independently before or after invoking this method). The search tools available to me could not conclusively trace every caller's surrounding logic in `chunk_result_verifier.rs` and `chunk_executor/mod.rs` to determine whether a redundant checkpoint-hash check exists elsewhere that would make this specific gap non-exploitable in production replay/state-sync flows today. Given this uncertainty and that the feature is explicitly gated as "before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS," the current exploitability for mainnet is uncertain/likely low until that feature path is active, though the code-level invariant break is real and would become a proof-acceptance issue once the state-checkpoint/trading-native root feature ships without this fix.

### Recommendation
Extend `ensure_match_transaction_info` to compute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on either side) against the locally computed equivalents, failing loudly (as with the other `ensure!` checks) rather than silently returning `Ok(())`. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` (or any checkpoint-hash-dependent feature) is enabled, to prevent replay-verify and chunk-executor state-sync tooling from certifying incorrect state roots as valid.

### Proof of Concept
Not independently reproducible from the indexed code alone: exploitation would require constructing a divergent local execution/replay whose write-set hash and event root match but whose resulting Sparse Merkle / hot-state / position-state root differs, and observing that `ensure_match_transaction_info` still returns `Ok(())`. The code-level absence of the checkpoint-hash comparison is directly visible in the cited lines; a full end-to-end PoC would require running the chunk executor or `db-tool replay-on-archive` with a crafted divergent state root, which I could not execute in this read-only analysis.

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
