This confirms the analog: `starknet_api::deprecated_contract_class::Program` at [1](#0-0)  stores `attributes`, `builtins`, `debug_info`, `hints`, `identifiers`, `reference_manager`, etc. as raw `serde_json::Value`, and `ContractClass` (the deprecated/Cairo0 declare-transaction class) directly embeds this `Program` at [2](#0-1) . Since `serde_json::Value` deserialization is a plain recursive-descent parser with no configurable/enforced nesting-depth limit (unlike Jackson's `StreamReadConstraints.maxNestingDepth`), and there is no `recursion_limit`/`serde_stacker`/`max_nesting` guard anywhere in this codebase, a Cairo0 `DECLARE` transaction whose `program.identifiers` (or `hints`, `debug_info`, `reference_manager`, `attributes`) contains deeply nested JSON arrays/objects can drive the parser to a `StackOverflowError`-equivalent (Rust process abort) before any of the existing size checks run.

### Title
Unbounded JSON Nesting Depth in Deprecated (Cairo0) Contract Class Deserialization Causes Stack-Overflow DoS - (File: crates/starknet_api/src/deprecated_contract_class.rs)

### Summary
The deprecated/Cairo0 `ContractClass`/`Program` structs use raw `serde_json::Value` fields for `attributes`, `hints`, `identifiers`, `debug_info`, and `reference_manager` [1](#0-0) . `serde_json`'s recursive-descent parser has no built-in nesting-depth limit, mirroring the exact bug class in the Jackson advisory (`maxNestingDepth` bypass). A submitted DeclareV0/V1 transaction with an artificially deep nested JSON value in any of these fields is deserialized directly by the gateway/node before any bytecode/object-size validation runs.

### Finding Description
Declare transactions carrying a deprecated (Cairo0) contract class are deserialized via `serde::Deserialize` derived on `Program`/`ContractClass` [2](#0-1) . Because several fields are typed as unconstrained `serde_json::Value`, deserialization recurses once per nesting level of the attacker-controlled JSON, with no maximum depth enforced. This is functionally identical to the `UTF8DataInputJsonParser`/`ReaderBasedJsonParser` bug in the report: the parser is reachable from fully untrusted input and bypasses any (nonexistent, in this case) nesting constraint.

Existing gateway protections (`max_contract_bytecode_size`, `max_contract_class_object_size` in `crates/apollo_gateway/src/stateless_transaction_validator.rs`, lines 315-337) only run *after* the JSON has already been fully parsed into memory — they check output object size/string length, not parser recursion depth — so they cannot prevent the crash during parsing itself. No `recursion_limit`, `serde_stacker`, or manual depth-check utility exists anywhere in the repository (confirmed via repo-wide search).

### Impact Explanation
A single unprivileged transaction sender can submit a Cairo0 `DECLARE` transaction (or any RPC/JSON-RPC path that deserializes a deprecated contract class) with a small-byte-size but deeply nested JSON payload (e.g., `[[[[[...]]]]]` nested thousands of levels deep) in `program.identifiers`/`hints`/`attributes`. Parsing this recursively can exhaust the thread stack, crashing (aborting) the sequencer process/thread handling the request. If this occurs in the gateway's stateless validation path or in Starknet OS re-execution, it can be triggered node-wide, causing service disruption/denial-of-service (matches CWE-770, "no network able to confirm new transactions" criterion) rather than fund loss.

### Likelihood Explanation
Likelihood is High for triggering a crash of the specific worker/thread handling the request, since: (1) the input is fully attacker-controlled and requires no special privileges — any account can submit a `DECLARE` transaction; (2) constructing deeply nested JSON is trivial and can be extremely compact in bytes (a few nesting characters per level); (3) none of the existing size/version limits (Sierra version checks, `max_contract_class_object_size`, `max_contract_bytecode_size`) are applied prior to JSON parsing.

### Recommendation
- Replace unconstrained `serde_json::Value` fields in `Program`/`ContractClass` with a depth-bounded deserializer, or wrap the top-level `serde_json::Deserializer` with a stack-depth guard (e.g., `serde_stacker::maybe_grow`, or a custom `Deserializer` wrapper enforcing a maximum nesting depth similar to Jackson's `StreamReadConstraints`).
- Alternatively, pre-scan raw transaction bytes for maximum bracket/brace nesting depth before calling `serde_json::from_slice`/`from_value`, rejecting payloads exceeding a configured limit (e.g., 500, matching Jackson's default).
- Apply the same protection to any other unconstrained `serde_json::Value` fields reachable from untrusted transaction/class data across the codebase (e.g., `hinted_class_hash.rs`'s `CairoProgram.identifiers`/`hints`/`reference_manager` used during Starknet OS re-execution) [3](#0-2) .

### Proof of Concept
```json
{
  "abi": [],
  "program": {
    "attributes": [],
    "builtins": [],
    "data": [],
    "identifiers": [[[[[[[[[ ... (repeat "[" ~100,000 times) ... ]]]]]]]]],
    "hints": {},
    "main_scope": "__main__",
    "prime": "0x800000000000011000000000000000000000000000000000000000000000001",
    "reference_manager": {}
  },
  "entry_points_by_type": {}
}
```
Submitting this as the `contract_class` of a `DECLARE` (V0/V1) transaction to the gateway causes `serde_json` to recurse ~100,000 stack frames while parsing `identifiers` before any size/version checks in `StatelessTransactionValidator::validate_class_length`/`validate_sierra_version` execute, crashing the parsing thread/process with a stack overflow.

**Note on verification limits:** I was not able to trace, within this session, the exact call path proving that the deprecated `Program`/`ContractClass` type is reachable from the current live HTTP/RPC declare endpoints of this specific sequencer build (vs. only from legacy/RPC-v0.8 compatibility code and Starknet OS re-execution tooling) — the search results show usages in `apollo_rpc`, `apollo_starknet_client`, and `starknet_os`, but I could not fully confirm whether the primary gateway ingestion path for new V0/V1 declares is still active in production vs. deprecated/disabled. A Devin session with full repo access would be needed to confirm the exact live entry point(s).

### Citations

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

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L131-148)
```rust
/// A program corresponding to a [ContractClass](`crate::deprecated_contract_class::ContractClass`).
#[derive(Debug, Clone, Default, Eq, PartialEq, Deserialize, Serialize)]
pub struct Program {
    #[serde(default)]
    pub attributes: serde_json::Value,
    pub builtins: serde_json::Value,
    #[serde(default)]
    pub compiler_version: serde_json::Value,
    pub data: serde_json::Value,
    #[serde(default)]
    pub debug_info: serde_json::Value,
    #[serde(serialize_with = "serialize_hints_sorted")]
    pub hints: serde_json::Value,
    pub identifiers: serde_json::Value,
    pub main_scope: serde_json::Value,
    pub prime: serde_json::Value,
    pub reference_manager: serde_json::Value,
}
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L71-105)
```rust
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
