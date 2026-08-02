This confirms the analog. I have enough context to report the finding.

### Title
`ensure_match_transaction_info` never validates the state-checkpoint / hot-state / position-state root hashes, letting replay-verify accept a corrupted checkpoint root as matching an archived `TransactionInfo` - (File: `types/src/transaction/mod.rs`)

### Summary
The MIPS report's core lesson is that a hash-comparison/commitment-consistency check that the spec (or the system's own design) requires is silently skipped, so a divergent, out-of-spec state is accepted as valid. The Aptos-native analog is `TransactionOutput::ensure_match_transaction_info`, the function used by db-tool's `replay_on_archive` (and by `aptos-debugger`/`aptos-move/cli`) to prove that locally re-executed output matches the authenticated, chain-committed `TransactionInfo`. It checks status, gas, write-set hash, and event-root hash, but explicitly skips `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` — all of which are committed, consensus-verified fields inside `TransactionInfoV1` once `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` are enabled.

### Finding Description
`TransactionInfo`/`TransactionInfoV1` carries multiple checkpoint roots that are hashed into the transaction-accumulator leaf and thus consensus-verified: [1](#0-0) 

The `TransactionInfoV1.position_state_checkpoint_hash` is populated by execution when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled, and `hot_state_checkpoint_hash` when `HOT_STATE_ROOT_IN_TXN_INFO` is enabled: [2](#0-1) [3](#0-2) 

However, `ensure_match_transaction_info` — the function that replay/verify tooling uses to assert "the transaction output I just recomputed matches what's on the authenticated ledger" — only checks `status`, `gas_used`, the write-set hash (`state_change_hash`), and `event_root_hash`. It has a standing `TODO(trading-native)` acknowledging it ignores the checkpoint hashes entirely: [4](#0-3) 

This function is the sole correctness gate used by `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which re-executes archived transactions with `AptosVMBlockExecutor` and calls `ensure_match_transaction_info` against the `TransactionInfo` pulled from backup/archive storage: [5](#0-4) 

Because the state/hot-state/position checkpoint hashes are never compared, a state-checkpoint root (Sparse Merkle Tree root of the world state, or the native-position Jellyfish Merkle root) that diverges between local re-execution and the archived, consensus-signed ledger is never detected by this tool. The write-set hash and event-root hash checks alone do **not** transitively guarantee the checkpoint root is correct, because the checkpoint root is derived by folding per-transaction write sets into a running Sparse/Jellyfish Merkle tree across the whole block — a bug in that folding step (e.g., in `DoStateCheckpoint`, hot-state promotion, or `position_summary_at_commit`) would corrupt the checkpoint hash while leaving each individual transaction's write-set hash and event-root hash correct.

### Impact Explanation
Replay-verify (`db-tool replay-on-archive`) is one of the primary tools operators and auditors use to detect execution/consensus divergence from an archived ledger, including hard-fork-relevant execution bugs. Since the checkpoint-hash fields are silently excluded from the comparison, a bug that corrupts the committed state-checkpoint root, hot-state root, or native-position state root (all of which are authenticated via the transaction accumulator and signed by validators) would pass replay-verify undetected. This directly matches the "Hard-fork-only divergence during commit, replay, restore, or proof verification" and "wrong ... state proof accepted as valid" impact categories: a tool whose entire purpose is catching state-commitment mismatches has a documented blind spot on exactly the fields (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) that represent the authenticated world-state root.

### Likelihood Explanation
The gap is not hypothetical — it is explicitly acknowledged in the source as a `TODO(trading-native)` blocking `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` enablement in production/replay-verify workflows. As `TRANSACTION_INFO_V1`, `HOT_STATE_ROOT_IN_TXN_INFO`, and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are rolled out (permanent feature flags per `features.move`), any latent bug in the state-checkpoint computation path becomes silently unverifiable through this tool until the comparator is fixed. It requires no attacker action — only a divergence bug in the checkpoint-computation code, which this comparator is specifically supposed to catch and currently cannot.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the expected `TransactionInfo`) against the locally recomputed checkpoint roots, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` in any environment where `replay_on_archive` (or similar tooling) is relied upon for state-integrity verification.

### Proof of Concept
1. Enable `TRANSACTION_INFO_V1` + `HOT_STATE_ROOT_IN_TXN_INFO` (or `COMPUTE_TRADING_NATIVE_STATE_ROOTS`) so `TransactionInfoV1.hot_state_checkpoint_hash` / `position_state_checkpoint_hash` are populated and committed to the ledger accumulator.
2. Introduce (or hit, via an existing bug) a divergence in the checkpoint-root computation path (e.g., `execution/executor/src/workflow/do_state_checkpoint.rs` or `position_summary_at_commit` in `storage/aptosdb/src/db/aptosdb_writer.rs`) that produces a different checkpoint root than the one signed into the archived ledger, while leaving each transaction's write set and events (and thus `state_change_hash`/`event_root_hash`) unchanged.
3. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` over the affected version range.
4. Observe that `execute_and_verify`'s call to `ensure_match_transaction_info` at [6](#0-5)  returns `Ok(())` and reports zero failed transactions, despite the state-checkpoint root being wrong.

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

**File:** types/src/block_executor/config.rs (L173-189)
```rust
    pub fn with_features(mut self, features: &Features) -> Self {
        self.hotness_in_epilogue = features.is_hotness_in_epilogue_enabled();
        self.transaction_info_v1 = features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1: the hot state root rides in
        // TransactionInfoV1's hot_state_checkpoint_hash field, which V0 lacks.
        self.hot_state_root_in_txn_info = features.is_hot_state_root_in_txn_info_enabled()
            && features.is_transaction_info_v1_enabled();
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
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
