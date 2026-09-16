Based on my investigation, I found a plausible analog but could not fully confirm the recursion is truly unbounded and reachable from an unprivileged declarer in this codebase without further verification, so I'll present findings with appropriate caveats.

### Title
Unbounded recursive JSON parsing/sorting of a declared Cairo0 class's `identifiers` field may cause stack-overflow DoS - ([File: crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs])

### Summary
`compute_cairo_hinted_class_hash` deserializes an attacker-controlled Cairo0 declared contract class into a `CairoProgram`/`CairoContractDefinition` structure whose `identifiers` field is a raw `serde_json::Value` [1](#0-0) , then calls `sort_json_value` to recursively walk and sort that value before re-serializing and hashing it [2](#0-1) . This is analogous to CVE-2018-19881, where recursive parsing of an untrusted, deeply-nested structure (SVG in MuPDF) caused stack exhaustion.

### Finding Description
`compute_deprecated_class_hash` / `compute_cairo_hinted_class_hash` deserialize the raw JSON of a declared Cairo0 contract class (`ContractClass`/`CairoProgram`) where the `identifiers` field is intentionally typed as `serde_json::Value` because its structure is not fixed [3](#0-2) . `serde_json::Value` parsing and the accompanying `sort_json_value` recursive-descent routine (used to canonicalize the value for deterministic hashing) walk nested objects/arrays recursively with no explicit depth limit that I could confirm in the visible code. If an attacker crafts a declare-v0/v1 transaction whose class JSON contains a sufficiently deeply nested `identifiers` (or similar `serde_json::Value` fields), both the initial `serde_json::from_str`/`from_slice` parse and the subsequent recursive `sort_json_value` traversal could consume stack proportional to nesting depth, potentially crashing the sequencer process handling declare-transaction hash computation. This logic is invoked when computing/validating a Cairo0 class hash, i.e., during declare transaction processing, which is reachable by any unprivileged contract declarer.

### Impact Explanation
If reachable and unbounded, a stack-overflow abort during class-hash computation on a node processing a submitted declare transaction would crash the process (gateway, batcher, or OS re-execution), which is a denial-of-service — it could stop a node/sequencer from confirming new transactions if triggered broadly.

### Likelihood Explanation
Low-to-uncertain. I could not confirm within the available index whether (a) `sort_json_value`'s implementation and depth, (b) whether any global request/class-size limits (e.g., `max_contract_class_object_size`, `max_contract_bytecode_size` seen in `apollo_gateway`'s stateless validator tests) effectively bound nesting depth before this code path is reached, and (c) whether `serde_json`'s own recursion limit (or `#[serde(rename)]`/arbitrary_precision feature configuration) already guards against this in practice. The gateway does enforce `max_contract_class_object_size` and `max_contract_bytecode_size` on the overall declared class [4](#0-3) , which bounds total size but not necessarily nesting depth (a highly nested but small JSON document can still have thousands of nesting levels within a small byte budget).

### Recommendation
Verify whether `sort_json_value` and the `serde_json` deserialization paths used for declared Cairo0 classes (`hinted_class_hash.rs`, `CairoProgram`, `CairoContractDefinition`) impose an explicit recursion/nesting-depth limit independent of overall payload size. If not, add an explicit depth check during JSON parsing/sorting of untrusted, declarer-controlled class content (e.g., using an iterative traversal with an explicit stack, or a max-depth guard that rejects declare transactions exceeding a safe nesting limit) before further validation or hashing.

### Proof of Concept
Not confirmed. I could not locate the implementation of `sort_json_value` itself in the accessible index to verify it lacks depth checking, nor confirm the exact call path from an RPC-submitted `DeclareV1Transaction` (with `DeprecatedContractClass`) through to `compute_cairo_hinted_class_hash`/`compute_deprecated_class_hash` in the gateway's validation flow versus only being used in Starknet OS re-execution. Given this uncertainty, I recommend a Devin session with full repository/file access to: (1) locate and inspect `sort_json_value`'s implementation, (2) trace the exact call path from `apollo_gateway`/`apollo_rpc` declare-transaction handling to `compute_cairo_hinted_class_hash`, and (3) construct a proof-of-concept declare-v0/v1 payload with deeply nested `identifiers` JSON to empirically test for a stack overflow. [5](#0-4)

### Citations

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L23-30)
```rust
/// Our version of the cairo contract definition used to deserialize and re-serialize a modified
/// version for a hash of the contract definition.
///
/// The implementation uses `serde_json::Value` extensively for the unknown/undefined structure, and
/// the correctness of this implementation depends on the following features of serde_json:
///
/// - feature `raw_value` has to be enabled for the thrown away `program.debug_info`
/// - feature `arbitrary_precision` has to be enabled, as there are big integers in the input
```

**File:** crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs (L90-105)
```rust

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L505-529)
```rust
#[test]
fn test_declare_contract_class_size_too_long() {
    let config_max_contract_class_object_size = 100; // Some arbitrary value, which will fail the test.
    let tx_validator = StatelessTransactionValidator {
        config: StatelessTransactionValidatorConfig {
            max_contract_class_object_size: config_max_contract_class_object_size,
            ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
        },
    };
    let contract_class = SierraContractClass {
        sierra_program: create_sierra_program(&MIN_SIERRA_VERSION),
        ..Default::default()
    };
    let contract_class_length = serde_json::to_string(&contract_class).unwrap().len();
    let tx = rpc_declare_tx(declare_tx_args!(), contract_class);

    assert_matches!(
        tx_validator.validate(&tx).unwrap_err(),
        StatelessTransactionValidatorError::ContractClassObjectSizeTooLarge {
            contract_class_object_size, max_contract_class_object_size
        } if (
            contract_class_object_size, max_contract_class_object_size
        ) == (contract_class_length, config_max_contract_class_object_size)
    )
}
```
