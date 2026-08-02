### Title
Replay-verify comparator omits checkpoint-hash validation, allowing corrupted state/hot-state/position roots to pass as verified - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` is the comparator used by replay-verify and replay-on-archive tooling to confirm that a locally re-executed transaction output matches the `TransactionInfo` committed to the ledger accumulator (and thus authenticated by validator signatures). The function explicitly skips validating `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash`, per its own TODO comment, so a divergence in any of those checkpoint roots between local re-execution and the authenticated on-chain `TransactionInfo` will not be detected by this check.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates only status, gas used, write-set hash (`state_change_hash`), and event root hash between a `TransactionOutput` and its corresponding `TransactionInfo`. The trailing comment makes the gap explicit: [2](#0-1) 

This means the checkpoint-related fields of `TransactionInfoV1` — `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — defined at [3](#0-2)  are never cross-checked against locally recomputed roots in this comparator.

This function is called from the replay/debug tooling (`storage/db-tool/src/replay_on_archive.rs` and `aptos-move/aptos-debugger/src/aptos_debugger.rs`), which is exactly the tooling responsible for detecting authenticated-state divergence during replay against archived, signed ledger data. The `COMPUTE_TRADING_NATIVE_STATE_ROOTS` and `HOT_STATE_ROOT_IN_TXN_INFO` feature flags (`types/src/on_chain_config/aptos_features.rs` lines 203-209) gate whether these roots are committed to `TransactionInfoV1` at all: [4](#0-3) . Once either flag is turned on in production, the position/hot-state Merkle roots become part of the consensus-authenticated `TransactionInfo`, but the very tool meant to independently verify replay correctness against that authenticated data silently ignores mismatches in those fields.

### Impact Explanation
If a bug in position-tree computation (`storage/aptosdb/src/db/aptosdb_writer.rs` `position_summary_at_commit`), hot-state root computation, or any other logic feeding `TransactionInfoV1`'s checkpoint hashes produces a wrong root at execution time, that wrong root is what gets accumulator-hashed and signed by validators (a genuine state-commitment divergence). `ensure_match_transaction_info` is the exact function that db-tool's replay-verify/replay-on-archive pipeline relies on to catch such divergences by replaying against archived data and comparing outputs. Because it does not compare the checkpoint hashes, a hard-fork-class bug corrupting the position state tree or hot-state tree would go undetected by this safety net — replay-verify would report success even though the authenticated ledger state diverged from correct VM execution. This matches the "Hard-fork-only divergence during commit, replay ... or proof verification" and "committed state that differs from the correct VM result" impact categories.

### Likelihood Explanation
The gap only becomes consequential once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` are enabled and `TransactionInfoV1` is in active use; the code and its own comment ("...before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`") indicate these features are being staged/rolled out rather than in mainnet's default committed feature set today. I was not able to confirm from the index whether these flags are currently enabled on mainnet — this reduces certainty of current-day exploitability but the code path itself, and the acknowledged detection gap, are real and unpatched as written. Likelihood is best characterized as "engineering-debt gap awaiting exploitation once the trading-native/hot-state features go live," not an immediately triggerable mainnet bug today.

### Recommendation
Extend `ensure_match_transaction_info` to also verify `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the `TransactionInfo` variant and computable from local execution/state-checkpoint output), consistent with the TODO comment's own suggested fix, before `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` are enabled in production.

### Proof of Concept
Not independently reproducible from static analysis alone: exploiting this requires (1) the trading-native/hot-state feature flags being enabled and (2) an independent root-computation bug elsewhere (e.g., in `position_summary_at_commit`) that produces an incorrect checkpoint root at execution/commit time. This report documents the detection-gap root cause in `ensure_match_transaction_info` itself, which is locally provable from the code and its own acknowledging comment; a full end-to-end PoC would additionally require constructing/triggering such a root-computation bug, which was not found in this scan.

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
