### Title
Uncontrolled recursion in `sort_json_value` during Cairo0 declare class-hash computation causes sequencer stack-overflow crash - (File: `crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs`)

### Summary
`compute_cairo_hinted_class_hash` computes the "hinted class hash" for a deprecated (Cairo0) contract class by recursively walking the entire serialized contract (including the attacker-fully-controlled `abi: serde_json::Value` field and other free-form JSON fields such as `identifiers`, `reference_manager`, `hints`) via `sort_json_value`, which recurses once per nesting level with no depth bound. [1](#0-0) [2](#0-1) 

### Finding Description
`CairoContractDefinition` deserializes the deprecated (Cairo0) contract class's `abi` and other structural fields as raw `serde_json::Value`, explicitly noting they have "no schema definition": [3](#0-2) 

`compute_cairo_hinted_class_hash` re-serializes the whole class to a `serde_json::Value` and calls `sort_json_value` on it to normalize key ordering before hashing: [2](#0-1) 

`sort_json_value` is a plain (non-tail) recursive function with no depth check:
```
fn sort_json_value(value: serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(map) => { ... map.into_iter().map(|(k, v)| (k, sort_json_value(v))) ... }
        serde_json::Value::Array(arr) => { serde_json::Value::Array(arr.into_iter().map(sort_json_value).collect()) }
        other => other,
    }
}
``` [4](#0-3) 

This is the same bug class as CVE-2021-28040 (uncontrolled recursion on deeply-nested attacker-controlled structured input): a caller can submit a Cairo0 `DECLARE` transaction whose `abi` field (or `identifiers`/`reference_manager`/`hints` fields inside `program`, all typed as opaque `serde_json::Value`) contains a deeply nested JSON array/object (e.g., `[[[[[...]]]]]`), and the recursion depth is proportional to the nesting depth the attacker chooses, unbounded by any explicit limit in this function. Unlike the blockifier's Cairo VM call-stack recursion (which is explicitly bounded by `RecursionDepthGuard`/`max_recursion_depth`, see `crates/blockifier/src/execution/entry_point.rs:706-733`), this JSON-sorting path has no equivalent guard. [5](#0-4) 

Whether this is reachable from a single unprivileged transaction submission depends on whether the sequencer computes this hinted hash synchronously as part of gateway validation or block execution for Cairo0 declare transactions (rather than only inside the Starknet OS/committer re-execution tooling). This module lives under `starknet_os`, and its exact call sites in the gateway/blockifier validation path were not fully confirmed within the scope of this investigation — I could not verify from the available index whether `compute_cairo_hinted_class_hash` is invoked during normal sequencer transaction processing (as opposed to only OS/proof-related re-execution flows). This should be verified directly in the repository before treating this as a confirmed, exploitable-in-production issue.

### Impact Explanation
If this function is invoked synchronously in the sequencer's transaction-processing critical path (gateway validation, mempool admission, or block execution) for Cairo0 `DECLARE` transactions, an attacker-crafted deeply nested `abi`/`identifiers`/`hints` JSON value would cause unbounded native stack recursion, leading to a stack overflow and a process crash (segfault/abort) of the sequencer — a denial of service reachable by a single unprivileged transaction submission, matching the "network unable to confirm new transactions" impact criterion.

### Likelihood Explanation
Likelihood is uncertain without confirming the call path from gateway/mempool/blockifier into `compute_cairo_hinted_class_hash`. Rust's default stack size (several MB) combined with `serde_json`'s own JSON parsing (also recursive, though `arbitrary_precision`/typical serde_json parsing has practical depth limits before hitting its own stack limits) generally requires tens of thousands of nesting levels to trigger a crash, which is easily embeddable in a contract-class JSON blob within normal size limits. If the function only runs in offline/OS re-execution/proving tooling and not in the live sequencer's transaction admission or block-building path, the practical severity for the live network is much lower.

### Recommendation
- Confirm whether `compute_cairo_hinted_class_hash` (or any other unbounded-recursion JSON walker) is invoked on attacker-supplied Cairo0 class fields during gateway validation, mempool admission, or blockifier execution.
- If so, replace the recursive `sort_json_value` with an iterative (stack-based, e.g., explicit `Vec`-based worklist) implementation, and/or enforce a maximum JSON nesting depth check before processing untrusted contract-class data (mirroring the existing `max_recursion_depth`/`RecursionDepthGuard` pattern used for Cairo VM call recursion).
- Apply the same defensive-depth-limit review to any other recursive JSON/structure walkers operating on attacker-controlled declare-transaction payloads.

### Proof of Concept
Submit a Cairo0 `DECLARE` transaction whose contract class `abi` field (typed as free-form `serde_json::Value`) is a deeply nested JSON array, e.g. `[[[[ ... ]]]]` nested to a depth sufficient to exceed the thread's stack size (tens of thousands of levels, well within typical transaction size limits). If `compute_cairo_hinted_class_hash` is reached during declare-transaction validation/hashing, `sort_json_value` will recurse once per nesting level and overflow the stack, crashing the sequencer process handling the transaction.

### Citations

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L35-51)
```rust
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
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

**File:** crates/blockifier/src/execution/entry_point.rs (L706-726)
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
