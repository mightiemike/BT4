### Title
Unbounded recursion in `sort_json_value` during deprecated (Cairo0) class hash computation can crash the Starknet OS re-execution process - (File: `crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs`)

### Summary
`sort_json_value()` recursively descends into `serde_json::Value::Object`/`Array` nodes with no depth limit, exactly analogous to the NLTK `JSONTaggedDecoder.decode_obj()` bug: a sufficiently deeply-nested JSON structure will exceed the native call stack and abort the process with a stack overflow (Rust has no catchable `RecursionError`, so the process is killed) rather than returning a graceful error.

### Finding Description
`compute_cairo_hinted_class_hash` serializes an entire deprecated (Cairo0) `ContractClass` to a `serde_json::Value` and calls `sort_json_value` to canonicalize key ordering before hashing: [1](#0-0) [2](#0-1) 

`sort_json_value` has no depth guard — every nested object/array level adds one Rust stack frame. The `abi` and `identifiers`/`reference_manager`/`hints` fields of `CairoContractDefinition`/`CairoProgram` are typed as raw, unconstrained `serde_json::Value`, so an attacker who controls the class body (the ABI JSON in particular) fully controls the nesting depth of the value passed into this function: [3](#0-2) [4](#0-3) 

`compute_cairo_hinted_class_hash` (and thus `sort_json_value`) is invoked from `compute_deprecated_class_hash`, used by the Starknet OS hint that loads a deprecated compiled class object into Cairo VM memory during OS re-execution of a block containing a `Declare` (Cairo0) transaction: [5](#0-4) [6](#0-5) 

### Impact Explanation
A single, unprivileged contract declarer can submit a legacy (Cairo0) `DECLARE` transaction whose ABI (or other unconstrained JSON fields, e.g. `program.identifiers`/`reference_manager`/`hints`) contains deeply nested JSON arrays/objects. When the Starknet OS later re-executes the block containing this declared class (to compute the deprecated class hash inside the Cairo VM hint), `sort_json_value`'s unbounded recursion overflows the stack and aborts the OS re-execution process — a crash of a core sequencer/prover-adjacent component that halts block re-execution/verification for that block, i.e., a denial-of-service reachable purely from a submitted transaction's contract-class content.

### Likelihood Explanation
Likelihood is moderate: the attacker only needs a validly-formed but pathologically-deeply-nested legacy contract class ABI/program JSON attached to a `Declare` transaction, no special privileges, keys, or timing are required. The severity depends on whether earlier gateway/mempool validation for Cairo0 declares imposes a nesting-depth or size limit on the ABI/program JSON before it reaches this OS hint; no such depth check was found in the reviewed code paths, only downstream size/format constraints on unrelated fields (e.g., compressed program size limits elsewhere in the codebase, which do not bound JSON nesting depth).

### Recommendation
Add an explicit recursion-depth (or iterative-with-explicit-stack) implementation to `sort_json_value`, mirroring the suggested NLTK fix, and reject/bound the nesting depth of untrusted JSON fields (`abi`, `identifiers`, `reference_manager`, per-hint values) at declare-transaction ingestion time (gateway validation) before they are recursively processed.

### Proof of Concept
Construct a Cairo0 `DECLARE` transaction whose contract class `abi` field is a deeply nested JSON array, e.g. (conceptually, analogous to the NLTK PoC):
```
depth = 100_000
abi_json = "[" * depth + "]" * depth
```
Embed this as the `abi` value of the declared `ContractClass`. When the Starknet OS re-executes the block containing this declaration and calls `compute_cairo_hinted_class_hash` → `sort_json_value` on the deserialized `serde_json::Value` tree, the recursive descent through `depth` nested `Value::Array` frames will exhaust the stack and abort the process.

Note: I was not able to fully verify, within the available index, whether the gateway/mempool validation pipeline for legacy `Declare` transactions imposes any JSON nesting-depth restriction upstream of this OS hint; confirming that would require a broader trace of the Cairo0 declare validation path, which the current search did not surface.

### Citations

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L37-51)
```rust
pub struct CairoContractDefinition<'a> {
    /// Contract ABI, which has no schema definition.
    pub abi: serde_json::Value,

    /// Main program definition.
    #[serde(borrow)]
    pub program: CairoProgram<'a>,

    /// The contract entry points.
    ///
    /// These are left out of the re-serialized version with the ordering requirement to a
    /// Keccak256 hash.
    #[serde(skip_serializing)]
    pub entry_points_by_type: HashMap<EntryPointType, Vec<EntryPointV0>>,
}
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L73-105)
```rust
pub struct CairoProgram<'a> {
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub attributes: Vec<AttributeScope>,

    #[serde(borrow)]
    pub builtins: Vec<Cow<'a, str>>,

    // Added in Starknet 0.10, so we have to handle this not being present.
    #[serde(borrow, skip_serializing_if = "Option::is_none")]
    pub compiler_version: Option<Cow<'a, str>>,

    #[serde(borrow)]
    pub data: Vec<Cow<'a, str>>,

    // Serialize as None for compatibility with Python.
    #[serde(borrow, serialize_with = "serialize_as_none")]
    pub debug_info: Option<&'a serde_json::value::RawValue>,

    // Important that this is ordered by the numeric keys, not lexicographically
    pub hints: BTreeMap<u64, Vec<serde_json::Value>>,

    pub identifiers: serde_json::Value,

    #[serde(borrow)]
    pub main_scope: Cow<'a, str>,

    // Unlike most other integers, this one is hex string. We don't need to interpret it, it just
    // needs to be part of the hashed output.
    #[serde(borrow)]
    pub prime: Cow<'a, str>,

    pub reference_manager: serde_json::Value,
}
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L117-132)
```rust
/// Recursively sorts all JSON objects by their keys.
/// This ensures deterministic serialization regardless of the `preserve_order` feature in
/// serde_json.
fn sort_json_value(value: serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(map) => {
            let sorted: BTreeMap<String, serde_json::Value> =
                map.into_iter().map(|(k, v)| (k, sort_json_value(v))).collect();
            serde_json::Value::Object(sorted.into_iter().collect())
        }
        serde_json::Value::Array(arr) => {
            serde_json::Value::Array(arr.into_iter().map(sort_json_value).collect())
        }
        other => other,
    }
}
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L154-167)
```rust
pub fn compute_cairo_hinted_class_hash(
    contract_class: &ContractClass,
) -> Result<Felt, HintedClassHashError> {
    // Serialize to Value, sort all objects by keys for deterministic output, then to bytes.
    // The sorting is necessary because serde_json with `preserve_order` feature enabled
    // maintains insertion order instead of sorting keys.
    // TODO(Meshi): Compute hinted hashes when loading serialized contracts from storage, before
    // deserializing, to avoid back-and-forth serde.
    let contract_value = serde_json::to_value(contract_class)?;
    let sorted_contract_value = sort_json_value(contract_value);
    let contract_definition_vec = serde_json::to_vec(&sorted_contract_value)?;
    let contract_definition: CairoContractDefinition<'_> =
        serde_json::from_slice(&contract_definition_vec)?;

```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L80-102)
```rust
pub fn compute_deprecated_class_hash(
    contract_class: &ContractClass,
) -> Result<Felt, HintedClassHashError> {
    let hinted_class_hash = compute_cairo_hinted_class_hash(contract_class)?;
    let contract_definition_vec = serde_json::to_vec(contract_class)?;
    let contract_definition: CairoContractDefinition<'_> =
        serde_json::from_slice(&contract_definition_vec)?;

    let FlatEntryPointFelts { external, l1_handler, constructor } =
        get_flat_entry_point_felts(&contract_definition.entry_points_by_type);
    let builtins = ascii_strs_as_felts(&contract_definition.program.builtins);
    let bytecode = hex_strs_as_felts(&contract_definition.program.data);

    let mut hash_state = HashState::<Pedersen>::new();
    hash_state.update_single(&DEPRECATED_COMPILED_CLASS_VERSION);
    hash_state.update_with_hashchain(&external);
    hash_state.update_with_hashchain(&l1_handler);
    hash_state.update_with_hashchain(&constructor);
    hash_state.update_with_hashchain(&builtins);
    hash_state.update_single(&hinted_class_hash);
    hash_state.update_with_hashchain(&bytecode);
    Ok(hash_state.finalize())
}
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/utils.rs (L84-86)
```rust
        // Insert hinted class hash.
        let hinted_class_hash = compute_cairo_hinted_class_hash(self)?;

```
