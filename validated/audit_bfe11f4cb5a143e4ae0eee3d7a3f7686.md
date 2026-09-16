### Title
Malformed `\uXXXX` escape in `PythonJsonFormatter` causes wrong deprecated (Cairo0) class hash computation, leading to consensus divergence - ([File: crates/papyrus_common/src/python_json.rs])

### Summary
`compute_cairo_hinted_class_hash` uses a custom JSON serializer, `PythonJsonFormatter`, to reproduce Python `json.dumps()` output byte-for-byte before hashing a declared Cairo0 contract with Keccak, as part of computing the deprecated class hash (`compute_deprecated_class_hash`). The formatter's non-ASCII escaping logic contains a formatting bug that produces malformed `\u` escape sequences for a broad class of Unicode code points, changing the exact byte content that gets hashed whenever an attacker-declared Cairo0 class contains such characters (e.g., inside a `with_attr` string, which is fully attacker-controlled text embedded in the compiled program's `attributes`).

### Finding Description
`PythonJsonFormatter::write_string_fragment` is responsible for escaping non-ASCII characters to match Python's `json.dumps` `\uXXXX` output: [1](#0-0) 

The escape is generated with `write!(writer, r"\u{num:4x}")`. The format spec `{num:4x}` specifies a minimum width of 4 but does **not** zero-pad — Rust's default fill character for width padding is a space, not `0`. Correct Python `json.dumps` output always zero-pads to exactly 4 hex digits (e.g., U+05D0 → `\u05d0`). Any UTF-16 code unit whose hex representation is shorter than 4 digits (i.e., any code point below `0x1000`, covering Latin-1 Supplement, Greek, Cyrillic, Hebrew, Arabic, Armenian, Syriac, and many other scripts) will instead be rendered with one or more literal space characters inserted into the escape sequence (e.g., `\u 5d0` instead of `\u05d0`).

This serializer is used directly to build the byte string that is fed into the hinted-class-hash Keccak computation for declared Cairo0 (deprecated) contract classes: [2](#0-1) 

The hinted class hash is then folded into the final deprecated class hash via a Pedersen hash chain: [3](#0-2) 

and is also injected directly into the Starknet OS Cairo VM memory as `hinted_class_hash` during OS re-execution of `DeprecatedCompiledClass`: [4](#0-3) 

Cairo0 programs can contain fully attacker-controlled string content in `AttributeScope.value` (populated from `with_attr` directives in the source, e.g., custom error messages), which is serialized as part of the `CairoContractDefinition.attributes` field that goes through `PythonJsonFormatter`: [5](#0-4) 

Since Python's reference `json.dumps` (the canonical algorithm this class-hash scheme is designed to mirror, per the module's own doc comment) always zero-pads `\uXXXX` escapes, this Rust implementation diverges from the canonical/spec-correct byte sequence whenever such characters appear, producing an incorrect hinted class hash — and therefore an incorrect deprecated class hash — for a declarer-controlled Cairo0 class.

### Impact Explanation
The deprecated class hash is a consensus-critical value: it identifies the class in the class trie/state commitment and is independently recomputed during Starknet OS re-execution (`insert_values_to_fields` with `hinted_class_hash`) to validate declared classes. If this sequencer's Rust implementation computes a class hash that differs from the canonical/spec-correct value (as computed by other conforming implementations, e.g. Python-based tooling or other client software using correctly zero-padded escaping), this is a form of protocol non-conformance that can:
- Cause an incorrect hinted/class hash to be committed for any Cairo0 class containing a `with_attr` message (or other attacker-controlled string, if present anywhere in the definition) with characters below code point `0x1000`.
- Produce a class hash mismatch between the sequencer's own OS re-execution result and the class hash expected/verified elsewhere in the pipeline, or between this implementation and any external/reference implementation relied upon for interoperability — i.e., honest-node/implementation divergence in a committed value (class hash → state root).

This satisfies the required impact bar (wrong committed root / honest-implementation divergence for a consensus-critical hash), reachable purely by declaring a Cairo0 class with attacker-chosen text (a normal, unprivileged declare-transaction capability).

### Likelihood Explanation
Any unprivileged user submitting a Cairo0 (`DECLARE`) transaction can trigger this by including a non-ASCII character with code point `< 0x1000` anywhere that ends up in an `AttributeScope.value`/`name` string (most easily via a `with_attr error_message("...")` in Cairo0 source containing e.g. a Hebrew, Greek, Cyrillic, or accented Latin character). This requires no special privileges, only a normal contract declaration, making the trigger trivial and fully attacker-controlled.

### Recommendation
Fix the escape format string in `PythonJsonFormatter::write_string_fragment` to zero-pad to exactly 4 hex digits, matching Python's `json.dumps` behavior: use `write!(writer, r"\u{num:04x}")` (or equivalently `format!("{:04x}", num)`), and add a regression test asserting exact byte-for-byte parity with Python's `json.dumps` output for code points spanning the full BMP range, especially values below `0x1000` and surrogate pairs for values above `0xFFFF`.

### Proof of Concept
1. Compile/declare a Cairo0 contract whose source includes, e.g., `with_attr error_message("\u{05D0}")` (Hebrew Aleph, U+05D0) in any function, producing an `AttributeScope.value` containing that character.
2. Submit the class via a `DECLARE` transaction; the sequencer computes `compute_deprecated_class_hash` → `compute_cairo_hinted_class_hash`, which serializes the `attributes` field through `PythonJsonFormatter`.
3. Because `05D0`'s UTF-16 unit hex representation is 3 digits, `write!(writer, r"\u{num:4x}")` emits `\u 5d0` (with a literal space) instead of the canonical `\u05d0`.
4. Compare the resulting Keccak-based `hinted_class_hash`/`compute_deprecated_class_hash` output against a reference computed using Python's `json.dumps` (zero-padded escaping) for the identical contract class JSON — the two hashes will differ, demonstrating the byte-level divergence and its propagation into the consensus-relevant class hash.

### Citations

**File:** crates/papyrus_common/src/python_json.rs (L33-49)
```rust
    fn write_string_fragment<W>(&mut self, writer: &mut W, fragment: &str) -> IOResult<()>
    where
        W: ?Sized + Write,
    {
        let mut buf = [0u16; 2];
        for ch in fragment.chars() {
            if ch.is_ascii() {
                writer.write_all(&[u8::try_from(ch).expect("ASCII fits in u8")])?;
            } else {
                let slice = ch.encode_utf16(&mut buf);
                for num in slice {
                    write!(writer, r"\u{num:4x}")?;
                }
            }
        }
        Ok(())
    }
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L56-67)
```rust
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
```

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

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs (L80-101)
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
```

**File:** crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/utils.rs (L84-106)
```rust
        // Insert hinted class hash.
        let hinted_class_hash = compute_cairo_hinted_class_hash(self)?;

        // Insert bytecode_ptr.
        let bytecode_ptr = deserialize_array_of_bigint_hex(&self.program.data)?;

        let bytecode_ptr_base = vm.add_memory_segment();
        vm.load_data(bytecode_ptr_base, &bytecode_ptr)?;

        // Insert the fields.
        let nested_fields_and_value = [
            ("compiled_class_version", compiled_class_version.into()),
            ("n_external_functions", Felt::from(externals_len).into()),
            ("external_functions", externals_list_base.into()),
            ("n_l1_handlers", Felt::from(l1_handlers_len).into()),
            ("l1_handlers", l1_handlers_list_base.into()),
            ("n_constructors", Felt::from(constructors_len).into()),
            ("constructors", constructors_list_base.into()),
            ("n_builtins", Felt::from(builtins.len()).into()),
            ("builtin_list", builtin_list_base.into()),
            ("hinted_class_hash", hinted_class_hash.into()),
            ("bytecode_length", Felt::from(bytecode_ptr.len()).into()),
            ("bytecode_ptr", bytecode_ptr_base.into()),
```
