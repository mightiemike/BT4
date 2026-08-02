## Analysis Summary

The HMX report's core lesson, reduced to an invariant: **a value used to gate/derive protocol state should be checked wherever downstream code assumes it matches the "real" computed state — if a code path silently skips that check, stale/incorrect derived state can be accepted as valid.**

I generated several candidate paths in Aptos-native commit/proof code (write-set-to-v1 conversion, hotness/auxiliary-info handling in `do_get_execution_output.rs`, transaction accumulator root computation, restore path in `restore_utils.rs`). Most either have parity assertions or are consistently derived from the same on-chain config across all validators. The strongest, self-proven candidate is a **documented, code-confirmed integrity gap** in `TransactionOutput::ensure_match_transaction_info`.

### Title
`ensure_match_transaction_info` fails to validate state/hot-state/position checkpoint hashes, allowing replay-verify and output-verification paths to accept a `TransactionInfo` whose committed checkpoint roots diverge from the locally re-executed state - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by chunk-execution and replay/debugger tooling to assert that a locally-computed `TransactionOutput` is consistent with an already-committed `TransactionInfo` (the authenticated, accumulator-committed record). It checks status, gas used, write-set hash, and event root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as unresolved.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates status, gas, write-set hash, and event root hash against a target `TransactionInfo`, but the trailing comment makes explicit that the checkpoint hashes are intentionally left unchecked: [2](#0-1) 

These checkpoint hashes are exactly the fields carrying the authenticated Merkle-committed state that is bound into the transaction accumulator (and hence into proofs): the state-checkpoint root, the hot-state root (`HOT_STATE_ROOT_IN_TXN_INFO`, gated at [3](#0-2) ), and the position/native-trading state root (`COMPUTE_TRADING_NATIVE_STATE_ROOTS`, gated at [4](#0-3) ). Both features are wired through `BlockExecutorConfigFromOnchain::with_features` at [5](#0-4) .

`ensure_match_transaction_info` is invoked from `execution/executor/src/chunk_executor/mod.rs` (used for output-list verification/replay) as well as from `aptos-move/aptos-debugger/src/aptos_debugger.rs` and `aptos-move/cli/src/commands.rs` (replay-verify tooling such as `replay_on_archive`). In all of these paths, this function is the sole cross-check that a re-executed `TransactionOutput` matches an on-disk/committed `TransactionInfo`. Because it skips the checkpoint-hash fields, a corrupted, stale, or wrongly-computed checkpoint root (hot-state root or position/native-trading state root) embedded in the committed `TransactionInfo` will not be caught by this verification, even though the write-set and event hash checks pass.

### Impact Explanation
If the `hot_state_checkpoint_hash` or `position_state_checkpoint_hash` fields of a committed `TransactionInfo` ever diverge from what local re-execution independently derives (e.g., due to a bug in hot-state promotion logic, sharded-execution position-root aggregation, or a storage/restore bug), `ensure_match_transaction_info` will report a successful match. This directly satisfies the "authenticated ... proof context" and "wrong accumulator root ... accepted as valid" impact categories: replay-verify and chunk-output-verification tooling — the mechanisms operators and auditors rely on to detect ledger divergence — would silently pass even though the authenticated proof-bearing root differs from the correct VM result. This is a proof-integrity blind spot in the exact verification function meant to catch such divergence.

### Likelihood Explanation
Both `HOT_STATE_ROOT_IN_TXN_INFO` and `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are newer feature flags (require `TRANSACTION_INFO_V1`, and the latter additionally requires `HOTNESS_IN_EPILOGUE`), so likelihood is contingent on their activation status on mainnet, which I could not fully confirm from the index (feature-flag default/activation state is determined by governance and framework `features.move`/on-chain config, not statically in this snippet). Regardless of current activation status, the gap is a real, developer-acknowledged blind spot in a security-relevant verification function that will become exploitable/impactful the moment these features are enabled or if any other TransactionInfoV1 checkpoint-producing path develops a bug — replay-verify would fail to flag it.

### Recommendation
Extend `ensure_match_transaction_info` to compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in `TransactionInfo`) against locally-recomputed equivalents before enabling or relying on `HOT_STATE_ROOT_IN_TXN_INFO` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS` in production, as the existing TODO already recommends.

### Proof of Concept
No dynamic PoC was run (index/tool access only). Static proof consists of: (1) the checked fields in `ensure_match_transaction_info` at [6](#0-5) , none of which cover checkpoint hashes; (2) the explicit acknowledgement comment at [7](#0-6) ; and (3) the call sites in `execution/executor/src/chunk_executor/mod.rs` and the replay-verify tooling (`aptos-debugger`, CLI `commands.rs`) that depend on this function as their sole cross-check.

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

**File:** types/src/on_chain_config/aptos_features.rs (L203-206)
```rust
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
```

**File:** types/src/on_chain_config/aptos_features.rs (L207-209)
```rust
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
