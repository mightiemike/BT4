### Title
`ensure_match_transaction_info` skips checkpoint-hash comparison, allowing state/hot-state/position root divergence to pass replay verification - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the integrity check used by chunk-executor commit validation, `aptos-debugger`, the CLI, and `db-tool`'s `replay_on_archive` to assert that a locally re-computed `TransactionOutput` matches the authenticated `TransactionInfo` (the leaf committed to the transaction accumulator and signed by validators). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but it never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` against the recomputed values.

### Finding Description [1](#0-0) 

The function's own comment discloses the gap: [2](#0-1) 

`ensure_match_transaction_info` validates:
- `status` vs `txn_info.status()`
- `gas_used` vs `txn_info.gas_used()`
- `write_set_hash` (hash of `self.write_set()`) vs `txn_info.state_change_hash()`
- `event_root_hash` vs `txn_info.event_root_hash()`

It does **not** validate `txn_info.state_checkpoint_hash()`, the new `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()` fields that `TransactionInfoV1` carries [3](#0-2) . These state/hot-state/position roots are computed separately in `DoStateCheckpoint::run`, gated by `hot_state_root_in_txn_info` and `compute_trading_native_state_roots` [4](#0-3) , and are the fields that get bound into the accumulator via `TransactionInfoV1`. Because `ensure_match_transaction_info` is the sole generic sanity check reused across `chunk_executor`, `aptos-debugger`, CLI, and `replay_on_archive`, any bug in state/hot-state/position summary computation (e.g., a wrong `LedgerStateSummary::update`, wrong `hot_root_hash()`, or wrong `compute_position_checkpoint`) is committed to the ledger accumulator and signed, and no downstream automated consistency check catches the divergence.

### Impact Explanation
This is a state-integrity/proof-binding gap: the very oracle that is supposed to detect "committed state differs from correct VM result" for checkpoint (state/hot-state/position) roots is a no-op for those fields. If any of the checkpoint-hash-producing code paths (`LedgerStateSummary::update`, `hot_root_hash`, `compute_position_checkpoint` in `do_state_checkpoint.rs`) diverges from the authenticated root already committed to the accumulator/ledger info, `ensure_match_transaction_info` will still report success. This directly undermines the "authenticated API or state-view output bound to the wrong version/root" guarantee for consumers relying on this function (chunk-executor validation during application of chunks, `replay_on_archive` used for security audits of historical state, and the debugger/CLI users trust for local replay checks). This meets the stated impact bar (wrong root accepted as valid, since the validation function that should catch a divergent root does not check it).

### Likelihood Explanation
The gap is triggered whenever `TransactionInfoV1` and any of `HOT_STATE_ROOT_IN_TXN_INFO` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS` are enabled — these are newly introduced, feature-flag-gated subsystems (`HOTNESS_IN_EPILOGUE=116`, `TRANSACTION_INFO_V1=117`, `HOT_STATE_ROOT_IN_TXN_INFO=123`, `COMPUTE_TRADING_NATIVE_STATE_ROOTS=122`) [5](#0-4) , and the code's own TODO explicitly flags this as a known, un-fixed gap ("Validate the checkpoint hashes here before enabling COMPUTE_TRADING_NATIVE_STATE_ROOTS"). This does not require a malicious actor — it only requires a latent bug in one of these new checkpoint-computation code paths to go undetected by the exact tooling meant to catch it. I could not fully verify (due to remaining iteration limits) whether `chunk_executor::mod.rs`, `aptos-debugger`, or `replay_on_archive.rs` call sites additionally cross-check these hashes elsewhere before or after calling `ensure_match_transaction_info`; that would need to be independently confirmed by reading those five call sites in full before treating this as fully verified end-to-end.

### Recommendation
Extend `ensure_match_transaction_info` to recompute and compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present on `TransactionInfoV1`) against values passed in from the checkpoint-output stage, matching the pattern already used for `state_change_hash` and `event_root_hash`. This should be done before `COMPUTE_TRADING_NATIVE_STATE_ROOTS` / `HOT_STATE_ROOT_IN_TXN_INFO` are enabled on mainnet, per the existing TODO.

### Proof of Concept
Not directly exploitable as a standalone PoC without reproducing a real divergence bug in `do_state_checkpoint.rs`'s state/hot-state/position summary computation; the finding is that if such a divergence occurs (accidentally or via a future regression), `ensure_match_transaction_info` — called from `execution/executor/src/chunk_executor/mod.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, `aptos-move/cli/src/commands.rs`, and `storage/db-tool/src/replay_on_archive.rs` — will report success anyway, since it structurally never reads `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` from `txn_info` in its comparison logic [1](#0-0) .

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

**File:** execution/executor/src/workflow/do_state_checkpoint.rs (L42-75)
```rust
        let last_checkpoint = state_summary.last_checkpoint();

        let state_checkpoint_hashes = Self::get_state_checkpoint_hashes(
            execution_output,
            known_state_checkpoints,
            last_checkpoint.root_hash(),
            "state",
        )?;
        let hot_state_checkpoint_hashes = execution_output
            .hot_state_root_in_txn_info
            .then(|| {
                Self::get_state_checkpoint_hashes(
                    execution_output,
                    known_hot_state_checkpoints,
                    last_checkpoint.hot_root_hash()?,
                    "hot_state",
                )
            })
            .transpose()?;

        let (position_state_summary, position_state_checkpoint_hashes) =
            if execution_output.compute_trading_native_state_roots {
                let persisted = persisted_position_state_summary
                    .expect("persisted position summary required when feature on");
                let (summary, hashes) = Self::compute_position_checkpoint(
                    execution_output,
                    parent_position_state_summary,
                    persisted,
                    known_position_state_checkpoints,
                )?;
                (Some(summary), Some(hashes))
            } else {
                (None, None)
            };
```

**File:** types/src/on_chain_config/aptos_features.rs (L187-209)
```rust
    /// epilogue: the promotion set is embedded into the block epilogue transaction
    /// payload (`BlockEpiloguePayload::V2`), and every transaction output in the block
    /// uses the V1 write-set format, which encodes hot-state changes in its serialized
    /// writes.
    HOTNESS_IN_EPILOGUE = 116,
    /// When enabled, execution assembles `TransactionInfoV1` instead of `TransactionInfoV0`.
    TRANSACTION_INFO_V1 = 117,
    /// Umbrella auth flag for the native-trading subsystem; the per-store
    /// flags below gate the actual writes. Both must be on to write.
    TRADING_NATIVE = 118,
    /// Gates native-position writes.
    NATIVE_POSITION = 119,
    /// Gates native-orderbook writes.
    NATIVE_ORDERBOOK = 120,
    /// Gates native-collateral writes.
    NATIVE_COLLATERAL = 121,
    /// When enabled, execution computes the native-position state root at the
    /// checkpoint stage and commits it to `TransactionInfoV1`, so it is
    /// consensus-verified. Requires `TRANSACTION_INFO_V1`.
    COMPUTE_TRADING_NATIVE_STATE_ROOTS = 122,
    /// When enabled, execution populates `TransactionInfoV1`'s hot state root hash, so it
    /// is committed to the ledger accumulator. Requires `TRANSACTION_INFO_V1`.
    HOT_STATE_ROOT_IN_TXN_INFO = 123,
```
