### Title
Native-position write-set extensions are excluded from the `WriteSet` crypto hash, decoupling committed position-state mutations from the transaction's `state_change_hash` - (File: types/src/write_set.rs)

### Summary
`WriteSet` derives `BCSCryptoHash`, so its cryptographic hash is computed over its BCS-serialized bytes. `WriteSetV0` marks both `hotness` and `extensions` fields as `#[serde(skip)]` [1](#0-0) , meaning any data in those fields is invisible to BCS serialization and therefore to the derived hash. The executor has an explicit workaround for the `hotness` case: when `onchain_config.hotness_in_epilogue()` is enabled, `DoGetExecutionOutput::convert_write_sets_to_v1` upgrades affected outputs' write sets to `WriteSetV1` (whose fields are all serialized, including `hotness`) before transaction infos/hashes are computed [2](#0-1) [3](#0-2) . No equivalent conversion is gated on the presence of `Extension::NativePosition` data added via `add_native_positions` [4](#0-3) .

### Finding Description
`add_native_positions` installs position-state (trading) writes into a `WriteSet`'s `extensions` bucket, which the storage commit applier consumes separately via `native_position_iter` to update durable position state [5](#0-4) . Because `extensions` (like `hotness`) is `#[serde(skip)]` on `WriteSetV0` [1](#0-0) , this data does not appear in the BCS bytes used to compute the `WriteSet`'s `BCSCryptoHash`, nor in the bytes persisted via `WriteSetSchema::encode_value` (`bcs::to_bytes(self)`) [6](#0-5) .

The only mechanism that promotes a `WriteSetV0` to `WriteSetV1` (which does serialize/hash these extra fields) is `convert_write_sets_to_v1`, called conditionally on `onchain_config.hotness_in_epilogue()` [2](#0-1) . That gate is tied specifically to the hot-state feature flag and is unrelated to whether a write set carries `Extension::NativePosition` data. If a transaction's write set carries native-position writes but is not otherwise converted to V1 (e.g., hot-state-in-epilogue is off, or the conversion path is only applied to block-epilogue outputs rather than every output with extensions), the resulting `WriteSet` hash used to build `TransactionInfo` (and hence the transaction accumulator leaf and any accumulator/Merkle proof over it) is computed as if the native-position writes did not exist.

### Impact Explanation
If this gap is real (see uncertainty note below), it breaks a core state-commitment invariant: durable ledger state (the position/native state store) would be mutated by data that is never bound into the transaction's committed hash. Two nodes (or a malicious full node serving state-sync/backup data) could apply different native-position write content for the same transaction while producing identical `TransactionInfo`/accumulator commitments, since the divergent bytes are outside the hashed/serialized region. This is a "committed state differs from the correct VM result" and "wrong accumulator root/proof accepted as valid" class of bug per the state-integrity gate, and would not be detectable by transaction-info or accumulator proof verification.

### Likelihood Explanation
Native-position (trading) writes are a newer, narrowly-scoped feature (`aptos-move/framework/position-natives`), and the `#[serde(skip)]` fields plus the existing, hotness-specific "convert to V1 before hashing" workaround strongly suggest the framework maintainers are already aware that skip-listed fields must be promoted to `WriteSetV1` before being hashed/persisted — but the promotion condition (`hotness_in_epilogue`) is not obviously coupled to `has_native_positions()`. I was not able to fully trace, within the available search budget, whether `add_native_positions` call sites (e.g. `execution/executor/src/workflow/do_state_checkpoint.rs`, `aptos-move/framework/position-natives/src/context.rs`) are always followed by a V1 conversion through some other path before `TransactionInfo` construction. This is the key open question that determines whether the bug is real or already mitigated elsewhere.

### Recommendation
Trace every code path that calls `WriteSet::add_native_positions` and confirm whether the resulting `WriteSet` is unconditionally converted to `WriteSetV1` (or otherwise has its extensions included in the hashed/serialized representation) before `TransactionInfo`/accumulator-leaf hashing and before persistence via `WriteSetSchema`. If any path allows an `extensions`-bearing `WriteSetV0` to be hashed/persisted without conversion, gate `convert_write_sets_to_v1` (or an equivalent check) on `write_set.has_native_positions() || !write_set.hotness_ref().is_empty()` rather than solely on `onchain_config.hotness_in_epilogue()`, or remove the `#[serde(skip)]` optimization entirely in favor of always using `WriteSetV1`.

### Proof of Concept
Could not be constructed with certainty in this session — a concrete PoC requires confirming the exact call graph from `add_native_positions` to `TransactionInfo` construction (i.e., whether `do_state_checkpoint.rs`'s native-position write sets bypass `convert_write_sets_to_v1`). This should be verified with a Devin session that can build/run the executor test harness (e.g., extending `execution/executor/src/tests/mod.rs`) to construct a transaction output with `Extension::NativePosition` writes but empty `hotness`, run it through `DoGetExecutionOutput`, and assert whether the resulting `TransactionInfo::state_change_hash` (or write-set hash) differs when the native-position payload is changed.

### Citations

**File:** types/src/write_set.rs (L759-767)
```rust
    /// Iterate the native-position bucket. Used by the storage
    /// commit applier; main-state consumers never see these entries.
    pub fn native_position_iter(&self) -> impl Iterator<Item = (&StateKey, &NativePositionOp)> {
        self.native_positions().into_iter().flat_map(|m| m.iter())
    }

    pub fn native_position_keys(&self) -> impl Iterator<Item = &StateKey> {
        self.native_positions().into_iter().flat_map(|m| m.keys())
    }
```

**File:** types/src/write_set.rs (L773-786)
```rust
    /// Install the native-position bucket. Mirrors [`add_hotness`]:
    /// expected to be called once per WriteSet, at VM-output
    /// materialization time.
    pub fn add_native_positions(&mut self, native_positions: BTreeMap<StateKey, NativePositionOp>) {
        let extensions = self.extensions_mut();
        assert!(
            !extensions
                .iter()
                .any(|e| matches!(e, Extension::NativePosition(_))),
            "native_positions should only be initialized once."
        );
        // TODO: the order here is important when there are more extensions.
        extensions.push(Extension::NativePosition(native_positions));
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

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L170-172)
```rust
        if onchain_config.hotness_in_epilogue() {
            Self::convert_write_sets_to_v1(&mut transaction_outputs);
        }
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L238-242)
```rust
    fn convert_write_sets_to_v1(transaction_outputs: &mut [TransactionOutput]) {
        transaction_outputs
            .iter_mut()
            .for_each(TransactionOutput::convert_write_set_to_v1);
    }
```

**File:** storage/aptosdb/src/schema/write_set/mod.rs (L54-57)
```rust
impl ValueCodec<WriteSetSchema> for WriteSet {
    fn encode_value(&self) -> Result<Vec<u8>> {
        bcs::to_bytes(self).map_err(Into::into)
    }
```
