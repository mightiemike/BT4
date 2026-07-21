The code is clear. Let me verify the exact type inference at the `serde_json::from_str` call site to confirm the failure mode.

The bug is confirmed. Here is the complete analysis:

---

### Title
Broken `abi: None` Round-Trip in `DeprecatedContractClass` ↔ `Cairo0Class` Protobuf Conversion Permanently Blocks P2P Sync of Affected Classes — (`crates/apollo_protobuf/src/converters/class.rs`)

### Summary

`From<deprecated_contract_class::ContractClass> for protobuf::Cairo0Class` encodes `abi: None` as the empty string `""`. The inverse `TryFrom<protobuf::Cairo0Class>` unconditionally calls `serde_json::from_str(&value.abi)`, which fails on `""` because the empty string is not valid JSON. The round-trip is permanently broken: any deprecated class whose `abi` field is `None` can never be transmitted or received over P2P, leaving syncing nodes with a permanently missing class.

### Finding Description

**Serialization side** — `From` impl, line 159:

```rust
None => "".to_string(),
``` [1](#0-0) 

`abi: None` is encoded as the empty string `""` in the `Cairo0Class.abi` protobuf field.

**Deserialization side** — `TryFrom` impl, line 131:

```rust
let abi = serde_json::from_str(&value.abi)?;
``` [2](#0-1) 

The inferred type is `Option<Vec<ContractClassAbiEntry>>` (from the struct definition). `serde_json::from_str::<Option<Vec<ContractClassAbiEntry>>>("")` returns `Err(...)` — the empty string is not valid JSON for any type. The `?` operator propagates this as a `ProtobufConversionError`, so the `TryFrom` always fails for any class whose `abi` was `None`.

**`abi: None` is a legitimate, documented production state:**

```rust
// Starknet does not verify the abi. If we can't parse it, we set it to None.
pub abi: Option<Vec<ContractClassAbiEntry>>,
``` [3](#0-2) 

It is also explicitly constructed in production reexecution code:

```rust
Ok((DeprecatedContractClass { program, entry_points_by_type, abi: None }).into())
``` [4](#0-3) 

**Concrete divergent value:**

| Step | Value |
|---|---|
| Input | `ContractClass { abi: None, … }` |
| After `From` | `Cairo0Class { abi: "", … }` |
| After `TryFrom` | `Err(ProtobufConversionError)` — parse failure on `""` |
| Expected | `ContractClass { abi: None, … }` |

### Impact Explanation

Any deprecated contract class with `abi: None` that a node attempts to share via P2P will be permanently undeliverable. The receiving node's `TryFrom` always returns `Err`, so the class is never written to the receiver's storage. The syncing node ends up with a missing class hash/class body in its state. If any transaction subsequently references that class hash, execution will fail with "class not found," producing wrong execution results for otherwise-valid inputs. This maps to: **Critical — wrong state / missing class hash from blockifier execution logic for accepted input.**

### Likelihood Explanation

`abi: None` is a documented fallback for any deprecated class whose ABI cannot be parsed, and is explicitly produced by production reexecution code. Any node that has ever stored such a class and attempts to serve it to a P2P peer will trigger the failure deterministically. No attacker action is required — the bug fires on any legitimate sync of an affected class.

### Recommendation

In the `From` impl, encode `abi: None` as `"null"` (valid JSON for `Option`) instead of `""`:

```rust
None => "null".to_string(),
```

In the `TryFrom` impl, add an explicit guard for the empty-string sentinel before calling `serde_json::from_str`, or rely solely on the `"null"` encoding after fixing the `From` side:

```rust
let abi = if value.abi.is_empty() {
    None
} else {
    serde_json::from_str(&value.abi)?
};
```

### Proof of Concept

```rust
use starknet_api::deprecated_contract_class::ContractClass;
use apollo_protobuf::protobuf::Cairo0Class;

let original = ContractClass { abi: None, program: dummy_program(), entry_points_by_type: Default::default() };
let proto: Cairo0Class = original.clone().into();
assert_eq!(proto.abi, "");  // encodes as empty string
let roundtrip = ContractClass::try_from(proto);
// serde_json::from_str("") → Err; roundtrip is Err, not Ok(original)
assert!(roundtrip.is_err(), "round-trip must fail — bug confirmed");
``` [5](#0-4)

### Citations

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-137)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

        Ok(Self { program, entry_points_by_type, abi })
```

**File:** crates/apollo_protobuf/src/converters/class.rs (L141-139)
```rust

```

**File:** crates/apollo_protobuf/src/converters/class.rs (L148-159)
```rust
        let encoded_abi = match value.abi {
            Some(abi_entries) => {
                let mut abi_bytes = vec![];
                abi_entries
                    .serialize(&mut serde_json::Serializer::with_formatter(
                        &mut abi_bytes,
                        PythonJsonFormatter,
                    ))
                    .expect("ABI is not in the expected Pythonic JSON byte format");
                String::from_utf8(abi_bytes).expect("Failed decoding ABI bytes as utf8 string")
            }
            None => "".to_string(),
```

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L19-21)
```rust
    // Starknet does not verify the abi. If we can't parse it, we set it to None.
    #[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
    pub abi: Option<Vec<ContractClassAbiEntry>>,
```

**File:** crates/blockifier_reexecution/src/compile.rs (L118-118)
```rust
    Ok((DeprecatedContractClass { program, entry_points_by_type, abi: None }).into())
```
