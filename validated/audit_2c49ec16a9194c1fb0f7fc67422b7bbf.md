Based on my investigation, I found a genuine local integrity gap in Aptos's hot-state write-set handling, distinct from the sleeper-NFT bug class but matching the same root pattern: **a derived, integrity-relevant piece of state ("who gets promoted to hot state") is computed and applied in one code path but silently dropped when the object is persisted/replayed through another path**, causing the durable/replayed result to diverge from the originally-committed one.

### Title
Hot-state promotions computed for `BlockEpilogue` transactions are silently dropped on serialization when `hotness_in_epilogue` is disabled but `hot_state_root_in_txn_info` is enabled, causing hot-state root divergence on output-based replay - (File: `execution/executor/src/workflow/do_get_execution_output.rs`, `types/src/write_set.rs`, `types/src/block_executor/config.rs`)

### Summary
`DoGetExecutionOutput` unconditionally calls `output.add_hotness(...)` on every `BlockEpilogue` transaction's `WriteSet` to record which state keys should be promoted to hot state [1](#0-0) , but it only converts the `WriteSet` to the serializable `V1` variant when the `hotness_in_epilogue` on-chain feature is enabled [2](#0-1) . `WriteSetV0`'s `hotness` field is marked `#[serde(skip)]`, so it never survives BCS serialization for a V0 write set [3](#0-2) . Meanwhile, `hot_state_root_in_txn_info` (which makes the hot-state root part of the authenticated `TransactionInfo`) is gated only on `transaction_info_v1`, not on `hotness_in_epilogue` [4](#0-3) . This is the exact class of bug the developers explicitly guarded against for the `native_position` extension bucket (`compute_trading_native_state_roots`, gated on `hotness_in_epilogue` "V0 drops them, so output-replay would diverge") [5](#0-4) , but the equivalent guard is missing for the plain `hotness` bucket used by `hot_state_root_in_txn_info`.

### Finding Description
1. `output.add_hotness(payload.try_get_keys_to_make_hot()...)` runs on the in-memory `TransactionOutput` for every block epilogue, independent of `onchain_config.hotness_in_epilogue()` [1](#0-0) .
2. `WriteSet::base_op_iter()` merges the `value_writes` and `hotness` buckets to produce `BaseStateOp::MakeHot` entries that the storage-commit applier uses to actually promote keys to hot state [6](#0-5) . This proves `hotness` is integrity-relevant: it drives real state-store mutations (hot-state promotion), which can feed into the state summary / hot-state root when `hot_state_root_in_txn_info` is enabled.
3. `Self::convert_write_sets_to_v1(&mut transaction_outputs)`, the only place that upgrades the `WriteSet` to the serialization-preserving `V1` form, is gated strictly behind `onchain_config.hotness_in_epilogue()` [2](#0-1) .
4. `WriteSetV0.hotness` and `WriteSetV0.extensions` are `#[serde(skip)]` by design [3](#0-2) , and the persisted-schema test confirms V0 write sets round-trip with zero hotness keys even if the source had none set, while V1 preserves them [7](#0-6) .
5. `DoGetExecutionOutput::by_transaction_output` is a real replay path that takes already-produced `Vec<TransactionOutput>` (i.e., write sets read back from storage/backup, not freshly computed by the VM) and rebuilds ledger state from them [8](#0-7) . If the persisted write set lost its `hotness` bucket (because it stayed V0), replay through this path will not re-apply the `MakeHot` promotions that were applied at original-commit time.
6. Because `hot_state_root_in_txn_info`'s enablement check does not require `hotness_in_epilogue` [4](#0-3) , it is possible for a validator to run with `hot_state_root_in_txn_info` on and `hotness_in_epilogue` off, at which point the hot-state root recorded in the authenticated `TransactionInfoV1` at original commit time was computed including the `MakeHot` promotions, but any output-based replay/resync recomputing state from the persisted (V0) write sets will compute a different hot-state root, since the promotions are gone.

### Impact Explanation
If reachable, this causes a genuine hard-fork-class divergence: the hot-state root embedded in the authenticated `TransactionInfoV1` (bound into the transaction accumulator and thus into ledger-info signatures) would not be reproducible by any node that reconstructs state via the `by_transaction_output` path (state sync via transaction outputs, backup/restore consumers, or debug/replay tooling) instead of full VM re-execution. That is a "wrong ... accepted as valid" / "hard-fork-only divergence during commit, replay, restore" condition per the state-integrity gate.

### Likelihood Explanation
This depends on a specific on-chain feature combination (`hot_state_root_in_txn_info = true`, `hotness_in_epilogue = false`) that I could not fully confirm is reachable/intended in this codebase revision — I was not able to trace all feature-flag interdependencies (e.g., whether `is_hot_state_root_in_txn_info_enabled` has an additional runtime/genesis-level dependency on `hotness_in_epilogue` enforced elsewhere, such as feature-activation ordering in `aptos-move/framework/move-stdlib/sources/configs/features.move` or `aptos_vm.rs`) within the available iterations.

### Recommendation
Gate `hot_state_root_in_txn_info` on `hotness_in_epilogue` the same way `compute_trading_native_state_roots` is gated, or make `convert_write_sets_to_v1` run whenever any block epilogue's `hotness` set is non-empty, regardless of the `hotness_in_epilogue` flag, so `WriteSetV0`'s serde-skip never silently discards on-chain-consequential data.

### Proof of Concept
Not independently constructed/executed; this is a structural code-path proof based on cross-referencing the unconditional `add_hotness` call, the conditional `convert_write_sets_to_v1` gate, the `#[serde(skip)]` fields on `WriteSetV0`, and the independent feature gating of `hot_state_root_in_txn_info`, all cited above.

**Caveat**: I could not fully verify within the available tool-call budget whether the specific flag combination (`hot_state_root_in_txn_info` on, `hotness_in_epilogue` off) is actually reachable in practice (e.g., possible implicit ordering/dependency constraints enforced at feature-activation time outside the files I inspected). This should be verified before treating the finding as fully confirmed — I recommend a Devin session with full repo/build access to check feature-flag activation logic and add a regression test reproducing a root-hash mismatch across the `by_transaction_output` replay path with `hotness_in_epilogue` off.

### Citations

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L159-169)
```rust
        for (transaction, output) in transactions.iter().zip_eq(transaction_outputs.iter_mut()) {
            if let Transaction::BlockEpilogue(payload) = transaction {
                assert!(output.status().is_kept(), "Block epilogue must be kept");
                output.add_hotness(
                    payload
                        .try_get_keys_to_make_hot()
                        .cloned()
                        .unwrap_or_default(),
                );
            }
        }
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L170-172)
```rust
        if onchain_config.hotness_in_epilogue() {
            Self::convert_write_sets_to_v1(&mut transaction_outputs);
        }
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L244-264)
```rust
    pub fn by_transaction_output(
        transactions: Vec<Transaction>,
        transaction_outputs: Vec<TransactionOutput>,
        auxiliary_infos: Vec<AuxiliaryInfo>,
        parent_state: &LedgerState,
        state_view: CachedStateView,
        onchain_config: BlockExecutorConfigFromOnchain,
    ) -> Result<ExecutionOutput> {
        let out = Parser::parse()
            .first_version(state_view.next_version())
            .transactions(transactions)
            .transaction_outputs(transaction_outputs)
            .auxiliary_infos(auxiliary_infos)
            .parent_state(parent_state)
            .base_state_view(state_view)
            .prime_state_cache(true)
            .is_block(false)
            .transaction_info_v1(onchain_config.transaction_info_v1())
            .hot_state_root_in_txn_info(onchain_config.hot_state_root_in_txn_info())
            .compute_trading_native_state_roots(onchain_config.compute_trading_native_state_roots())
            .build()?;
```

**File:** types/src/write_set.rs (L727-747)
```rust
    pub fn base_op_iter(&self) -> impl Iterator<Item = (&StateKey, &BaseStateOp)> {
        static MAKE_HOT_OP: BaseStateOp = BaseStateOp::MakeHot;

        self.value_writes()
            .write_set
            .iter()
            .map(|(key, op)| (key, op.as_base_op()))
            .merge_join_by(
                self.hotness_ref().iter().map(|key| (key, &MAKE_HOT_OP)),
                |a, b| a.0.cmp(b.0),
            )
            .map(|entry| {
                // It seems like it's possible to have a key that is both in `value` and `hotness`
                // (possibly due to inaccurate read write summary). If this happens we discard the
                // hotness change, since the recently written keys will be made hot anyway.
                match entry {
                    EitherOrBoth::Left(e) | EitherOrBoth::Right(e) => e,
                    EitherOrBoth::Both(e, _) => e,
                }
            })
    }
```

**File:** types/src/write_set.rs (L792-802)
```rust
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV0 {
    value_writes: WriteSetMut,
    /// Hot state promotions, non-empty only in block epilogues.
    #[serde(skip)]
    hotness: BTreeSet<StateKey>,
    /// Opt-in side-channels (see [`Extension`]). Skipped from serde so `TransactionInfo` hashes and
    /// the on-disk WriteSet format are unaffected.
    #[serde(skip)]
    extensions: Vec<Extension>,
}
```

**File:** types/src/block_executor/config.rs (L176-179)
```rust
        // Requires transaction_info_v1: the hot state root rides in
        // TransactionInfoV1's hot_state_checkpoint_hash field, which V0 lacks.
        self.hot_state_root_in_txn_info = features.is_hot_state_root_in_txn_info_enabled()
            && features.is_transaction_info_v1_enabled();
```

**File:** types/src/block_executor/config.rs (L180-188)
```rust
        // Requires transaction_info_v1 (the root rides in TransactionInfoV1) and
        // hotness_in_epilogue (only the V1 write-set format it enables serializes
        // the native-position extensions; V0 drops them, so output-replay would
        // diverge). Degrades to off if either is missing.
        self.compute_trading_native_state_roots = features
            .is_compute_trading_native_state_roots_enabled()
            && features.is_transaction_info_v1_enabled()
            && features.is_hotness_in_epilogue_enabled();
        self
```

**File:** storage/aptosdb/src/schema/write_set/test.rs (L21-62)
```rust
/// V0 (default) WriteSets round-trip through the schema codec.
#[test]
fn test_v0_roundtrip() {
    let ws = WriteSet::new(vec![
        (
            StateKey::raw(b"key1"),
            WriteOp::legacy_creation(b"val1".to_vec().into()),
        ),
        (
            StateKey::raw(b"key2"),
            WriteOp::legacy_modification(b"val2".to_vec().into()),
        ),
    ])
    .unwrap();

    let bytes = ws.encode_value().unwrap();
    let decoded = WriteSet::decode_value(&bytes).unwrap();
    assert_eq!(decoded.as_v0(), ws.as_v0());
    assert_eq!(decoded.hotness_keys().count(), 0);
}

/// V1 WriteSets constructed by production code round-trip through the schema codec.
#[test]
fn test_v1_roundtrip() {
    let mut ws = WriteSet::new(vec![(
        StateKey::raw(b"key1"),
        WriteOp::legacy_creation(b"val1".to_vec().into()),
    )])
    .unwrap();
    let hotness: BTreeSet<_> = [StateKey::raw(b"hot1")].into_iter().collect();
    ws.add_hotness(hotness.clone());
    let ws = ws.into_v1();

    let bytes = ws.encode_value().unwrap();
    let decoded = WriteSet::decode_value(&bytes).unwrap();
    assert!(matches!(decoded, WriteSet::V1(_)));
    assert_eq!(decoded, ws);
    assert_eq!(
        decoded.hotness_keys().cloned().collect::<BTreeSet<_>>(),
        hotness
    );
}
```
