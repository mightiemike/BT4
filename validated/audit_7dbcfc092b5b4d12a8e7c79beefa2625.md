## Finding

### Title
Unbounded recursion in `sort_json_value` during Cairo0 declared-class hash computation enables stack-overflow DoS - (File: `crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs`)

### Summary
The reported mistune bug class is "unbounded recursive descent over attacker-controlled, arbitrarily-nested input, with no depth guard, causing the process to crash (CWE-674)." The sequencer codebase contains a directly analogous pattern in the Cairo0 (deprecated) contract class hashing routine used by the Starknet OS.

### Finding Description
`compute_cairo_hinted_class_hash` deserializes a declared Cairo0 contract class into `serde_json::Value` and calls `sort_json_value` to canonicalize key ordering before hashing: [1](#0-0) 

`sort_json_value` is a plain, unbounded recursive function: every nested JSON object or array descends one more Rust stack frame, with no maximum-depth check anywhere in the call chain: [2](#0-1) 

The fields that flow into this recursive sort are attacker-controlled and typed as unconstrained `serde_json::Value`: `abi`, `program.identifiers`, `program.reference_manager`, and `program.hints` all accept arbitrary nesting: [3](#0-2) 

`compute_cairo_hinted_class_hash` is invoked from `compute_deprecated_class_hash`, which is called while the Starknet OS loads a `DeprecatedCompiledClass` fact during transaction re-execution/proving: [4](#0-3) [5](#0-4) 

This code path is reached whenever the OS processes a Cairo0 `DECLARE` (V0/V1) transaction — a transaction type still supported for backward compatibility (e.g., v1-bound accounts), as exercised by `create_declare_tx`/`compute_deprecated_class_hash` in the OS flow tests: [6](#0-5) 

A single unprivileged account can submit a `DECLARE` transaction (V0 or V1) whose Cairo0 `contract_class.abi` (or `program.identifiers`/`reference_manager`/`hints`) contains a JSON structure nested to a few thousand levels (e.g. `[[[[...]]]]`). Unlike Cairo native/VM execution recursion, which is explicitly bounded by `RecursionDepthGuard`/gas accounting: [7](#0-6) 

`sort_json_value` has no such guard, no gas metering, and no structural depth limit is enforced during class deserialization/hashing.

### Impact Explanation
A Rust stack overflow from unbounded recursion aborts the process (it is not a recoverable panic in the general case), so any sequencer or prover node executing/re-executing a block containing such a malicious `DECLARE` transaction crashes. Because block validity depends on the Starknet OS successfully computing this hash to verify the declared class-hash preimage (`finalize_class_hash` in the Cairo OS code), this directly threatens the sequencer's/prover's ability to produce or verify blocks — a network-wide denial-of-service vector triggerable by a single `DECLARE` transaction from any unprivileged account, matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Likelihood is high for any node path that reaches this hashing routine: the attacker only needs to submit one `DECLARE` transaction (V0/V1) with an oversized/deeply-nested `abi` or `program` JSON field; there is no authentication or special privilege required, and Cairo0 declares remain a live code path (backward-compatibility redeclaration, v1-bound accounts). The main open question — which I could not fully verify given the available tooling — is whether the standard gateway/blockifier `DECLARE` validation path (as opposed to the Starknet OS re-execution path) also calls `compute_deprecated_class_hash`/`compute_cairo_hinted_class_hash` directly during normal transaction ingestion (this would broaden reachability beyond OS re-execution to every sequencer's mempool/execution path). This should be verified further, along with whether any existing size/depth limit on the declared class's raw JSON bytes (e.g., `max_bytecode_size`, ABI length limits found in `apollo_gateway`) incidentally bounds nesting depth — none of the size-related configs found in this investigation constrain nesting depth specifically.

### Recommendation
- Add an explicit recursion-depth guard (e.g., a `max_depth` parameter, similar to `RecursionDepthGuard`) to `sort_json_value`, rejecting (returning an error) any Cairo0 class whose `abi`/`program` JSON exceeds a small fixed nesting depth (e.g., 64).
- Alternatively, rewrite `sort_json_value` as an explicit stack-based iterative traversal instead of native recursion, eliminating the stack-overflow risk entirely.
- Enforce the same depth limit earlier, at gateway/stateless-transaction-validation time, so malformed classes are rejected before reaching the OS/blockifier hashing routines.
- Increase test coverage with a deeply-nested-JSON regression test analogous to the mistune POC (`compute_cairo_hinted_class_hash` on a class with ~10,000 nested arrays in `abi`).

### Proof of Concept
```rust
// Conceptual PoC against crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs
use serde_json::json;

fn build_deeply_nested_abi(depth: usize) -> serde_json::Value {
    let mut v = json!([]);
    for _ in 0..depth {
        v = json!([v]);
    }
    v
}

// Construct a Cairo0 ContractClass whose `abi` field is build_deeply_nested_abi(50_000),
// wrap it in a Declare V0/V1 transaction, and submit it to the sequencer.
// When the Starknet OS re-executes/validates this DECLARE transaction, it calls
// compute_deprecated_class_hash -> compute_cairo_hinted_class_hash -> sort_json_value,
// which recurses once per nesting level with no depth bound, overflowing the
// Rust call stack and aborting the sequencer/prover process.
```

### Citations

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L37-105)
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

/// This struct is used to define specific serialization behavior for the `CairoProgram::attributes`
/// field, to ensure the hinted class hash matches the original implementation in older versions of
/// Starknet.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AttributeScope {
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub accessible_scopes: Vec<serde_json::Value>,
    pub end_pc: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub flow_tracking_data: Option<serde_json::Value>,
    pub name: String,
    pub start_pc: usize,
    pub value: String,
}

// It's important that this is ordered alphabetically because the fields need to be in sorted order
// for the keccak hashed representation.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
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

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/utils.rs (L84-85)
```rust
        // Insert hinted class hash.
        let hinted_class_hash = compute_cairo_hinted_class_hash(self)?;
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L80-89)
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
```

**File:** crates/starknet_os_flow_tests/src/utils.rs (L110-121)
```rust
    let declare_args = match feature_contract.get_class() {
        ContractClass::V0(class) => {
            let class_hash = ClassHash(compute_deprecated_class_hash(&class).unwrap());
            execution_contracts.add_deprecated_contract(class_hash, class);
            declare_tx_args! {
                version: TransactionVersion::ONE,
                max_fee: if bootstrap { Fee::default() } else { Fee(1_000_000_000_000_000) },
                class_hash,
                sender_address,
                nonce,
            }
        }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L680-700)
```rust
// Ensure that the recursion depth does not exceed the maximum allowed depth.
struct RecursionDepthGuard {
    current_depth: Arc<RefCell<usize>>,
    max_depth: usize,
}

impl RecursionDepthGuard {
    fn new(current_depth: Arc<RefCell<usize>>, max_depth: usize) -> Self {
        Self { current_depth, max_depth }
    }

    // Tries to increment the current recursion depth and returns an error if the maximum depth
    // would be exceeded.
    fn try_increment_and_check_depth(&mut self) -> Result<(), EntryPointExecutionError> {
        *self.current_depth.borrow_mut() += 1;
        if *self.current_depth.borrow() > self.max_depth {
            return Err(EntryPointExecutionError::RecursionDepthExceeded);
        }
        Ok(())
    }
}
```
