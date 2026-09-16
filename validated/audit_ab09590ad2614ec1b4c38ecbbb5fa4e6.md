### Title
Incorrect regex-based string transformation in legacy class hinted-hash computation can desynchronize the Starknet OS re-execution hash from the declared class content - (File: `crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs`)

### Summary
The reported CVE-2022-31086 is a case of *incorrect regular expressions* used to validate/transform untrusted, attacker-controlled string content, allowing the regex to be bypassed by carefully crafted input. The closest reachable analog in this sequencer is `add_backward_compatibility_space` in `crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs`, which applies a chain of regexes over the JSON-serialized ABI/`identifiers` content of a **Cairo0 (legacy) declared class** — content that is fully attacker-controlled by whoever submits the `Declare` transaction — in order to reproduce a legacy Python hashing quirk that feeds directly into the consensus-critical `hinted_class_hash`, which is itself hashed into the committed `deprecated_class_hash` used by the Starknet OS. [1](#0-0) 

### Finding Description
`compute_cairo_hinted_class_hash` serializes the contract's `abi`/`program.identifiers` data (attacker-supplied for Cairo0 declared classes) into JSON text, and when `compiler_version` is absent it post-processes that text with `add_backward_compatibility_space` before hashing it with Keccak256: [2](#0-1) 

The post-processing uses four chained regexes operating on raw JSON string fragments rather than a proper JSON/string-aware parser:
```
let cairo_regex = Regex::new(r#""cairo_type"\s*:\s*"\(([^"]*)\)""#).unwrap();
let value_regex = Regex::new(r#""value"\s*:\s*"(.*?)""#).unwrap();
let paren_regex = Regex::new(r"\(([^()]+)\)").unwrap();
let pair_regex = Regex::new(r"([^:\s,]+):\s*([^,\s)]+)").unwrap();
``` [3](#0-2) 

- `value_regex` uses a non-greedy `"(.*?)"`  capture that stops at the *first* subsequent `"` character. It does not account for escaped quotes (`\"`) that are legal inside a JSON string value, so an attacker-crafted `value` field containing an embedded escaped quote will cause the regex to terminate the capture at the wrong boundary, transforming a different substring than the actual JSON string content.
- `paren_regex` only matches a single, non-nested level of parentheses (`[^()]+` excludes any nested `(`/`)`), so multi-level nested tuple types in `value`/`cairo_type` are only partially rewritten by a single `replace_all` pass, unlike a correct recursive/balanced-parenthesis parser.

Because this transformation exists purely to reproduce a historical formatting quirk from the original Python hashing implementation, any divergence between the intended (reference) behavior and this regex-based approximation — for crafted `abi`/`identifiers` content containing escaped quotes, nested tuples, or unusual whitespace/colon placement — changes the bytes that are hashed, and therefore changes the resulting `hinted_class_hash` and the final `compute_deprecated_class_hash` output used for the class commitment: [4](#0-3) 

This value is also injected directly into Starknet OS VM memory during re-execution of deprecated (Cairo0) compiled classes: [5](#0-4) 

The input to this whole pipeline — the `abi` and `program.identifiers` JSON — is supplied unmodified by the account that submits the Cairo0 `Declare` transaction; the stateless/stateful gateway validators do not deeply structurally validate the free-form `abi`/`identifiers` JSON content, only bytecode size and sierra-version-related fields for Cairo1 (`crates/apollo_gateway/src/stateless_transaction_validator.rs`), so a class declarer is the direct, unprivileged trigger for this code path.

### Impact Explanation
If a crafted (but otherwise valid) Cairo0 contract class is declared such that its ABI/identifier `value`/`cairo_type` strings hit the regex edge cases described above, the resulting `hinted_class_hash`/`deprecated_class_hash` computed here can diverge from what a correct, faithful re-implementation (or a differently-behaving node) would compute for the same class. Since this hash is committed as part of the class hash used in state/commitment and re-derived during Starknet OS re-execution, a divergence here can produce a wrong committed class hash, causing honest-node hash mismatches or re-execution/proof failures for that class — i.e., consensus-relevant state divergence, which falls under "wrong committed root or block hash" / "honest-node divergence" in the accepted-impact list.

### Likelihood Explanation
Medium. The trigger requires only a standard, unprivileged `Declare` transaction for a Cairo0 class with specially crafted `abi`/`identifiers` JSON (e.g., string values containing escaped quotes or multi-level nested parenthesized tuple types) — no special privileges, staking, or node compromise required. However, the actual effect (a real divergence versus the legacy Python reference implementation this code intentionally mimics) has not been executed/proven at runtime here; the code and its regression tests (`hinted_class_hash_test.rs`) already exercise some nested-tuple cases, so the exploitability window is narrower than a naive read suggests and depends on exact edge cases (escaped quotes, deeper nesting, unusual whitespace) not covered by the existing fixtures.

### Recommendation
Replace the four chained best-effort regexes in `add_backward_compatibility_space` with a proper string/JSON-aware parser (or a formally verified recursive-descent transform) that correctly tracks JSON string escaping and balanced/nested parentheses, so the legacy formatting transform is provably equivalent to the reference implementation for all valid ABI/identifier content, not only the cases covered by existing test fixtures. Add fuzz/property tests comparing this Rust implementation's output against the canonical Python reference for a wide corpus of adversarially crafted `value`/`cairo_type` strings (escaped quotes, deep nesting, unicode, mixed whitespace).

### Proof of Concept
Not independently executed; based on static analysis of the regex definitions and their attacker-controlled input path: [6](#0-5) 
A concrete PoC would require constructing a Cairo0 contract class JSON whose ABI contains an `identifiers` entry with `"value": "(a: felt, b: \"nested\\\"quote\")"` or a multi-level nested tuple `cairo_type`, declaring it, and comparing the resulting `hinted_class_hash`/`deprecated_class_hash` against a hash computed by a strict JSON-aware re-implementation of the same backward-compatibility transform — this comparison was not performed as part of this analysis and should be validated by a background engineering session with test-execution access.

### Citations

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L154-185)
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

    let mut string_buffer = vec![];

    let mut ser = serde_json::Serializer::with_formatter(&mut string_buffer, PythonJsonFormatter);
    contract_definition.serialize(&mut ser)?;

    let mut raw_json_output = String::from_utf8(string_buffer)?;
    if contract_definition.program.compiler_version.is_none() {
        add_backward_compatibility_space(&mut raw_json_output);
    }

    let mut keccak_writer = KeccakWriter::default();
    keccak_writer
        .write_all(raw_json_output.as_bytes())
        .expect("writing to KeccakWriter never fails");

    let KeccakWriter(hash) = keccak_writer;
    Ok(truncated_keccak(<[u8; 32]>::from(hash.finalize())))
}
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L191-220)
```rust
pub fn add_backward_compatibility_space(input: &mut String) {
    let cairo_regex = Regex::new(r#""cairo_type"\s*:\s*"\(([^"]*)\)""#).unwrap();
    let value_regex = Regex::new(r#""value"\s*:\s*"(.*?)""#).unwrap();
    let paren_regex = Regex::new(r"\(([^()]+)\)").unwrap();
    let pair_regex = Regex::new(r"([^:\s,]+):\s*([^,\s)]+)").unwrap();

    // Fix cairo_type, which must be a single type (either a simple type or a tuple).
    *input = cairo_regex
        .replace_all(input, |caps: &regex::Captures<'_>| {
            let inner = &caps[1];
            let fixed = pair_regex.replace_all(inner, "$1 : $2");
            format!("\"cairo_type\": \"({fixed})\"")
        })
        .into_owned();

    // Fix only (key: value) pairs inside (...) in "value".
    *input = value_regex
        .replace_all(input, |caps: &regex::Captures<'_>| {
            let raw_value = &caps[1];
            let fixed = paren_regex
                .replace_all(raw_value, |paren_caps: &regex::Captures<'_>| {
                    let inner = &paren_caps[1];
                    let fixed_inner = pair_regex.replace_all(inner, "$1 : $2");
                    format!("({fixed_inner})")
                })
                .into_owned();
            format!("\"value\": \"{fixed}\"")
        })
        .into_owned();
}
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

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/utils.rs (L84-85)
```rust
        // Insert hinted class hash.
        let hinted_class_hash = compute_cairo_hinted_class_hash(self)?;
```
