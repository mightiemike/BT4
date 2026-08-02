## Finding: Silent loss of hot-state promotions and native-position writes when a `WriteSet::V0` carrying non-empty `hotness`/`extensions` is BCS-serialized for storage

### Title
Silent data loss when persisting `WriteSet::V0` with populated `hotness`/`extensions` fields due to `#[serde(skip)]` — corrupts durable write-set data without error - (File: `types/src/write_set.rs`)

### Summary
`WriteSetV0` marks its `hotness` and `extensions` fields with `#[serde(skip)]` [1](#0-0) , while the newer `WriteSetV1` variant serializes both fields normally [2](#0-1) . `WriteSet::add_hotness()` and `WriteSet::add_native_positions()` mutate these fields generically through `hotness_mut()`/`extensions_mut()` regardless of whether the underlying enum variant is `V0` or `V1` [3](#0-2) [4](#0-3) . If such mutations are applied to a `WriteSet::V0` instance that is never converted to `V1` before being BCS-serialized (e.g., for the `WriteSetSchema` DB column that stores raw `bcs::to_bytes(self)` [5](#0-4) ), the `hotness` promotions and `NativePosition` extension writes are silently dropped from the serialized bytes — with no error, no panic, and no size/consistency check — because `serde(skip)` fields are simply omitted from serialization.

### Finding Description
The write-set persistence layer trusts `bcs::to_bytes(&write_set)` to faithfully capture the VM's committed state changes [5](#0-4) . But `WriteSetV0` cannot represent `hotness`/`extensions` on the wire at all — those fields exist only in-memory for `V0` (`#[serde(skip)]`), whereas `WriteSetV1` was introduced specifically to carry them through serialization [2](#0-1) . The codebase acknowledges the migration is necessary via `TransactionOutput::convert_write_set_to_v1` / `DoGetExecutionOutput::convert_write_sets_to_v1` [6](#0-5) , implying that outputs must be upgraded to `V1` before being committed whenever hotness/extension data is present. However, `add_hotness`/`add_native_positions` are exposed as generic `WriteSet` methods with only an in-memory assertion that they are "initialized once" [7](#0-6) [8](#0-7)  — neither method enforces or checks that the receiver is already `V1`, and nothing in `WriteSetSchema::encode_value` rejects serializing a `V0` variant that has non-empty `hotness`/`extensions`. Any call path that populates these fields on a `V0`-tagged `WriteSet` and then persists it directly (bypassing the V0→V1 conversion step) will produce a **shorter, structurally different BCS payload** that decodes back into a `WriteSet::V0` with an *empty* `hotness` set and *no* `NativePosition` extension — silently discarding data that the VM actually computed and that consensus/execution regarded as part of the committed output.

### Impact Explanation
Hot-state promotions and native-position writes are part of the executor's real output and are consumed downstream for hot-state root computation and native-position storage commit (`native_position_iter`, consumed "via `WriteSet::native_position_iter`" per the doc comment on `NativePositionOp` [9](#0-8) ). If these are silently dropped from the durable `WriteSetSchema` record, the write set persisted and later replayed/queried from storage differs from the VM's actual computed result for that transaction — a committed-state-corruption class bug: the DB now holds a value (an empty hotness/extension set) that is provably wrong relative to the transaction that was actually executed, satisfying the "committed state differs from the correct VM result / corrupts durable ledger data" gate. Because storage restore, replay, and hot-state root reconstruction all read this raw `WriteSet` back from `WriteSetSchema`, any consumer relying on the persisted hotness/position bucket would silently reconstruct incomplete or incorrect state, with no error surfaced anywhere in the path.

### Likelihood Explanation
I was not able to fully trace, within the remaining investigation budget, whether every real commit path (in particular `write_set_db.rs::commit_write_sets`, and any restore/replay path that rebuilds `WriteSet` objects directly) always performs the `convert_write_set_to_v1` upgrade before invoking `add_hotness`/`add_native_positions` and before calling `encode_value`. The structural weakness — silent, un-guarded field loss keyed purely on enum variant tag, with only a runtime `assert!` for "initialized once" and no variant-compatibility check — is proven directly in `types/src/write_set.rs`. Whether it is reachable on every production commit path (vs. only in the specific V1-upgrade call sites that current code always exercises) is the part I could not fully confirm with certainty in the time available.

### Recommendation
Make `add_hotness` and `add_native_positions` fail (return `Result`/panic) if called while `self` is still `WriteSet::V0`, forcing callers to upgrade to `V1` first, or have these setters implicitly promote to `V1` themselves rather than silently mutating a field that cannot round-trip through serialization. Additionally, add a debug-mode invariant in `WriteSetSchema::encode_value` (or in `WriteSet` itself) that rejects/asserts when serializing a `V0` variant whose `hotness`/`extensions` are non-empty, so any latent violation fails loudly at commit time instead of silently truncating durable data.

### Proof of Concept
Conceptual repro (illustrating the exact corrupted value):
1. Construct `let mut ws = WriteSetMut::new(..).freeze().unwrap();` — this yields `WriteSet::V0` by construction [10](#0-9) .
2. Call `ws.add_hotness(some_non_empty_btreeset)` — succeeds silently because `hotness_mut()` only requires the field be empty, not that the variant be `V1` [7](#0-6) .
3. Persist via `WriteSetSchema::encode_value(&ws)` → `bcs::to_bytes(&ws)`; because `WriteSetV0::hotness` is `#[serde(skip)]`, the resulting bytes contain **no trace** of the hotness set [1](#0-0) .
4. `decode_value` on those bytes returns a `WriteSet::V0` whose `hotness` is the default empty `BTreeSet`, silently diverging from the originally-computed `ws`.

This demonstrates the exact corrupted value (empty hotness/extensions on read-back) and the precise root cause (`#[serde(skip)]` combined with variant-agnostic mutators), matching the "own interpretation causing silently wrong committed value" pattern from the seed report, but I could not fully verify within budget whether the current call graph always avoids triggering step 2–3 in that order on a real commit path — this remains the open uncertainty.

### Citations

**File:** types/src/write_set.rs (L504-508)
```rust
/// Native-position write produced by a transaction. Type-distinct from [`WriteOp`] so the compiler
/// refuses to mix native-position entries into the main-state bucket.
///
/// Carried inside [`Extension::NativePosition`] on a `WriteSet`. The storage commit applier (in
/// `aptos-db`) consumes it via [`WriteSet::native_position_iter`].
```

**File:** types/src/write_set.rs (L596-615)
```rust
    fn hotness_mut(&mut self) -> &mut BTreeSet<StateKey> {
        match self {
            Self::V0(ws) => &mut ws.hotness,
            Self::V1(ws) => &mut ws.hotness,
        }
    }

    fn extensions_ref(&self) -> &Vec<Extension> {
        match self {
            Self::V0(ws) => &ws.extensions,
            Self::V1(ws) => &ws.extensions,
        }
    }

    fn extensions_mut(&mut self) -> &mut Vec<Extension> {
        match self {
            Self::V0(ws) => &mut ws.extensions,
            Self::V1(ws) => &mut ws.extensions,
        }
    }
```

**File:** types/src/write_set.rs (L753-786)
```rust
    pub fn add_hotness(&mut self, hotness: BTreeSet<StateKey>) {
        let field = self.hotness_mut();
        assert!(field.is_empty(), "hotness should only be initialized once.");
        *field = hotness;
    }

    /// Iterate the native-position bucket. Used by the storage
    /// commit applier; main-state consumers never see these entries.
    pub fn native_position_iter(&self) -> impl Iterator<Item = (&StateKey, &NativePositionOp)> {
        self.native_positions().into_iter().flat_map(|m| m.iter())
    }

    pub fn native_position_keys(&self) -> impl Iterator<Item = &StateKey> {
        self.native_positions().into_iter().flat_map(|m| m.keys())
    }

    pub fn has_native_positions(&self) -> bool {
        self.native_positions().is_some_and(|m| !m.is_empty())
    }

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

**File:** types/src/write_set.rs (L820-827)
```rust
/// Like [`WriteSetV0`], but serializes the hotness and extension
/// buckets alongside the value write set.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV1 {
    value_writes: WriteSetMut,
    hotness: BTreeSet<StateKey>,
    extensions: Vec<Extension>,
}
```

**File:** types/src/write_set.rs (L870-877)
```rust
    pub fn freeze(self) -> Result<WriteSet> {
        // TODO: add structural validation
        Ok(WriteSet::V0(WriteSetV0 {
            value_writes: self,
            hotness: BTreeSet::new(),
            extensions: Vec::new(),
        }))
    }
```

**File:** storage/aptosdb/src/schema/write_set/mod.rs (L54-57)
```rust
impl ValueCodec<WriteSetSchema> for WriteSet {
    fn encode_value(&self) -> Result<Vec<u8>> {
        bcs::to_bytes(self).map_err(Into::into)
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
