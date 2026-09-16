### Title
Sequencer panics on Cairo-0 class with missing `CONSTRUCTOR` key in `entry_points_by_type` during constructor dispatch - (File: `crates/blockifier/src/execution/contract_class.rs`)

### Summary
`CompiledClassV0::constructor_selector` indexes a `HashMap<EntryPointType, Vec<EntryPointV0>>` with the `[]` operator instead of `.get()`, which panics if the `Constructor` key is absent. The map is populated verbatim from the deployer/declarer-supplied `entry_points_by_type` JSON with no normalization step that guarantees all three `EntryPointType` variants are present, so any transaction sender can declare a Cairo-0 class whose JSON simply omits the `"CONSTRUCTOR"` key (rather than providing an empty array, which is only a convention followed by the reference compiler, not enforced by validation). This is the same bug class as CVE-2017-6415: an untrusted, attacker-crafted structured input (a class/DEX file) is missing an expected field, and the parser dereferences/indexes it unconditionally, producing a crash.

### Finding Description
`CompiledClassV0::constructor_selector`: [1](#0-0) 
uses direct `HashMap` indexing (`self.entry_points_by_type[&EntryPointType::Constructor]`), which panics with "key not found" if `EntryPointType::Constructor` is not a key in the map — this happens before the subsequent `.first()?` (which would have safely handled an *empty* vector) is ever reached.

The `entry_points_by_type` field is deserialized straight from the declared class body with no default-filling of missing variants: [2](#0-1) 
`DeprecatedContractClass.entry_points_by_type` is a plain `HashMap<EntryPointType, Vec<EntryPointV0>>`: [3](#0-2) 
Standard serde `HashMap` deserialization only inserts keys that are actually present in the JSON object — there is no `#[serde(default)]` mechanism to force all three `EntryPointType` variants (`CONSTRUCTOR`, `EXTERNAL`, `L1_HANDLER`) to exist. All fixture files in the repo happen to include `"CONSTRUCTOR": []` explicitly (a compiler convention), but nothing in the deserialization/validation path enforces this for a hand-crafted DECLARE transaction body.

Notably, class-hash computation for deprecated classes is defensive and does **not** require the key to exist: [4](#0-3) 
which uses `.get(&ty).unwrap_or(&vec![])` — so a class omitting the `CONSTRUCTOR` key hashes and declares successfully with no error, silently deferring the crash to constructor-invocation time.

`constructor_selector()` is called on the constructor-dispatch path used by every contract deployment (via `DEPLOY_ACCOUNT` or the `deploy` syscall): [5](#0-4) 

### Impact Explanation
A single unprivileged transaction sender can:
1. Submit a `DECLARE` transaction (V0/V1) for a Cairo-0 class whose JSON `entry_points_by_type` omits the `"CONSTRUCTOR"` key entirely (only `EXTERNAL`/`L1_HANDLER` present, or even neither). This declare succeeds since class-hash computation tolerates missing keys.
2. Submit a `DEPLOY_ACCOUNT` transaction or an `INVOKE` that calls the `deploy` syscall targeting that class hash, triggering `execute_constructor_entry_point`, which calls `compiled_class.constructor_selector()` and panics on the missing-key indexing.

If this panic is not caught by a `catch_unwind`/panic-isolation boundary around in-process VM execution in the sequencer's batcher/execution pipeline, the panic can abort the block-building/execution worker thread or process, preventing the sequencer from completing block production — a network unable to confirm new transactions. Because the malformed class and the deploy attempt are part of the block's transaction set, the same input is processed deterministically by every honest node re-executing the block (e.g., in the Starknet OS re-execution / other full nodes), so this is a reproducible denial-of-service rather than a divergence bug.

### Likelihood Explanation
Likelihood is high for anyone attempting it deliberately: constructing a DECLARE transaction body with a hand-edited `entry_points_by_type` JSON that omits the `CONSTRUCTOR` key requires no special privileges, no race condition, and no unusual gas/fee tricks — it is a pure malformed-input crafting exercise reachable by any unprivileged transaction sender.

### Recommendation
- Replace the panicking `HashMap` index in `CompiledClassV0::constructor_selector` with `.get(&EntryPointType::Constructor).and_then(|v| v.first())...` (mirroring the safe pattern already used in `get_flat_entry_point_felts`/`insert_entry_points`).
- Alternatively/additionally, normalize `entry_points_by_type` at declare/gateway validation time so all three `EntryPointType` variants are guaranteed present (inserting empty vectors for missing ones), consistent with how `EntryPointByType::from_hash_map` already defaults missing entries via `.unwrap_or(&vec![])`.
- Add a regression test that declares (or directly constructs a `CompiledClassV0`) with a Cairo-0 class missing the `CONSTRUCTOR` key and exercises `constructor_selector`/deploy, asserting no panic occurs.

### Proof of Concept
1. Craft a Cairo-0 `DECLARE` (V0/V1) transaction whose `contract_class.entry_points_by_type` JSON is `{"EXTERNAL": [...], "L1_HANDLER": []}` (no `"CONSTRUCTOR"` key at all).
2. Submit the DECLARE transaction; it is accepted because class-hash computation (`get_flat_entry_point_felts`) tolerates the missing key via `.unwrap_or(&vec![])`.
3. Submit a `DEPLOY_ACCOUNT` transaction (or an `INVOKE` using the `deploy` syscall) referencing this class hash.
4. Execution reaches `execute_constructor_entry_point` → `compiled_class.constructor_selector()` → `self.entry_points_by_type[&EntryPointType::Constructor]` panics with "key not found", since the map has no `Constructor` entry (verified at `crates/blockifier/src/execution/contract_class.rs:344`).

Note on residual uncertainty: I could not fully verify, within the available tool budget, whether the sequencer's block-execution pipeline wraps entry-point execution in a panic-isolation boundary (`catch_unwind`) that would downgrade this from a process-level crash to a contained transaction failure. If such isolation exists and converts the panic into a reverted transaction (rather than crashing the block-building worker/process), the severity would be lower than described (a wasted transaction rather than a network-halting DoS); this should be confirmed by checking the batcher/execution-worker call sites that invoke `CallEntryPoint::execute`/`execute_constructor_entry_point` for any `std::panic::catch_unwind` wrapping.

### Citations

**File:** crates/blockifier/src/execution/contract_class.rs (L342-345)
```rust
impl CompiledClassV0 {
    fn constructor_selector(&self) -> Option<EntryPointSelector> {
        Some(self.entry_points_by_type[&EntryPointType::Constructor].first()?.selector)
    }
```

**File:** crates/blockifier/src/execution/contract_class.rs (L384-399)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq)]
pub struct CompiledClassV0Inner {
    #[serde(deserialize_with = "deserialize_program")]
    pub program: Program,
    pub entry_points_by_type: HashMap<EntryPointType, Vec<EntryPointV0>>,
}

impl TryFrom<DeprecatedContractClass> for CompiledClassV0 {
    type Error = ProgramError;

    fn try_from(class: DeprecatedContractClass) -> Result<Self, Self::Error> {
        Ok(Self(Arc::new(CompiledClassV0Inner {
            program: sn_api_to_cairo_vm_program(class.program)?,
            entry_points_by_type: class.entry_points_by_type,
        })))
    }
```

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L16-27)
```rust
/// A deprecated contract class.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ContractClass {
    // Starknet does not verify the abi. If we can't parse it, we set it to None.
    #[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
    pub abi: Option<Vec<ContractClassAbiEntry>>,
    pub program: Program,
    /// The selector of each entry point is a unique identifier in the program.
    // TODO(Yair): Consider changing to IndexMap, since this is used for computing the
    // class hash.
    pub entry_points_by_type: HashMap<EntryPointType, Vec<EntryPointV0>>,
}
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L51-64)
```rust
fn get_flat_entry_point_felts(
    entry_points_by_type: &HashMap<EntryPointType, Vec<EntryPointV0>>,
) -> FlatEntryPointFelts {
    fn flatten_entry_points(
        entry_points: &HashMap<EntryPointType, Vec<EntryPointV0>>,
        ty: EntryPointType,
    ) -> Vec<Felt> {
        entry_points
            .get(&ty)
            .unwrap_or(&vec![])
            .iter()
            .flat_map(|ep| [ep.selector.0, Felt::from(ep.offset.0)])
            .collect()
    }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L581-600)
```rust
    // Ensure the class is declared (by reading it).
    let compiled_class = state.get_compiled_class(ctor_context.class_hash).map_err(|error| {
        ConstructorEntryPointExecutionError::new(
            EntryPointExecutionError::from(error)
                .annotated(TrackedResource::CairoSteps, strip_vm_frames),
            &ctor_context,
            None,
        )
    })?;
    let Some(constructor_selector) = compiled_class.constructor_selector() else {
        // Contract has no constructor.
        return handle_empty_constructor(
            compiled_class,
            context,
            &ctor_context,
            calldata,
            *remaining_gas,
        )
        .map_err(|error| ConstructorEntryPointExecutionError::new(error, &ctor_context, None));
    };
```
