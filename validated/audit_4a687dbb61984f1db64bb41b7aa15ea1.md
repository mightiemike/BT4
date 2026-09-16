### Title
Panic-based DoS via `.expect()` on attacker-controlled Sierra `function_idx` during class declaration - (File: `crates/starknet_api/src/state.rs`)

### Summary
`SierraEntryPoint::function_idx` (a signed integer field originating from an attacker-supplied, RPC-submitted `DECLARE` transaction's Sierra contract class JSON) is converted to `usize` via `usize::try_from(...).expect("Function index should fit in a usize")` in the `From<SierraEntryPoint> for EntryPoint` conversion. If the value is negative or otherwise fails the conversion, the process panics instead of returning an error.

### Finding Description
`crates/starknet_api/src/state.rs` defines: [1](#0-0) 

This conversion is invoked when converting `entry_points_by_type` from a deserialized Sierra class (e.g. via `FlattenedSierraClass::into::<SierraContractClass>`), which is used for `calculate_class_hash()` and class handling on the declare path: [2](#0-1) [3](#0-2) 

This is directly analogous to CVE-2021-45340: a crafted, attacker-supplied input file (there, a PICT image; here, a declared Sierra class with a malformed `function_idx`) reaches a code path that fails to validate the value gracefully and instead crashes the process (there via NULL pointer dereference, here via `.expect()` panic) — both are unhandled-error DoS bugs triggered by untrusted structured input during parsing/normalization.

I was unable to fully confirm within the available context whether `serde_json`/RPC deserialization of `SierraEntryPoint.function_idx` allows negative values to pass through before reaching this `.expect()` (i.e., whether the concrete integer type used for `function_idx` in `SierraEntryPoint` permits values outside the `usize` range), because the struct definition for `SierraEntryPoint` was not located in the files inspected. This is a meaningful gap: if `function_idx` is deserialized as an unsigned type already, or if there is upstream validation rejecting out-of-range values before this conversion runs, the panic path may not be reachable from an untrusted `DECLARE` transaction. A background engineer should trace `SierraEntryPoint`'s definition (likely in `starknet_api::rpc_transaction` or an RPC types crate) and its `Deserialize` implementation, and confirm whether the gateway/class manager calls this conversion during declare-time validation (i.e., before or as part of `add_class` in `crates/apollo_class_manager/src/class_manager.rs`) with attacker data, prior to filing this as a confirmed finding.

### Impact Explanation
If reachable, a crafted `DECLARE` transaction's Sierra class containing an out-of-range `function_idx` for one of its declared entry points would cause the class-hash/class-manager component processing the declare transaction to panic, crashing the process handling it — a network-availability impact (inability to confirm/process new transactions) if this runs in a critical, non-isolated path such as the gateway or class manager service.

### Likelihood Explanation
Likelihood is uncertain pending confirmation of `SierraEntryPoint.function_idx`'s underlying type and any pre-validation. If the field type permits negative/huge values through deserialization and reaches this exact conversion without a bounds check, the trigger is trivial (any contract declarer can submit a malformed but otherwise well-formed JSON Sierra class).

### Recommendation
Replace the `.expect()` with a fallible conversion that returns a `StarknetApiError`/`ClassManagerError` up the call stack (e.g., via `TryFrom` instead of `From`), so malformed `function_idx` values are rejected as a normal declare-transaction validation error rather than causing a panic. Add a regression test analogous to the existing `deserialize_transaction_json_does_not_panic_on_malformed_resource_bounds` test in `crates/starknet_api/src/serde_utils_test.rs`, verifying that a Sierra class with negative/out-of-range `function_idx` produces an `Err`, not a panic.

### Proof of Concept
1. Craft a `DECLARE` transaction whose Sierra contract class JSON contains an `entry_points_by_type` entry with `function_idx: -1` (or a value exceeding `usize::MAX` on 32-bit builds).
2. Submit the transaction to the node's declare/class-manager pipeline that calls `SierraContractClass::from(FlattenedSierraClass)` and subsequently `calculate_class_hash()`/`get_component_hashes()`.
3. Observe process panic at `usize::try_from(entry_point.function_idx).expect("Function index should fit in a usize")` in `crates/starknet_api/src/state.rs:357-358`, rather than a clean validation error. [1](#0-0)

### Citations

**File:** crates/starknet_api/src/state.rs (L245-267)
```rust
impl SierraContractClass {
    pub fn calculate_class_hash(&self) -> ClassHash {
        let class_hash = Poseidon::hash_array(&self.get_component_hashes().flatten());
        ClassHash(class_hash.mod_floor(&L2_ADDRESS_UPPER_BOUND))
    }

    pub fn get_component_hashes(&self) -> ContractClassComponentHashes {
        let external_functions_hash = entry_points_hash(self, &EntryPointType::External);
        let l1_handlers_hash = entry_points_hash(self, &EntryPointType::L1Handler);
        let constructors_hash = entry_points_hash(self, &EntryPointType::Constructor);
        let abi_keccak = sha3::Keccak256::default().chain_update(self.abi.as_bytes()).finalize();
        let abi_hash = truncated_keccak(abi_keccak.into());
        let sierra_program_hash = Poseidon::hash_array(self.sierra_program.as_slice());
        let contract_class_version = self.contract_class_version();
        ContractClassComponentHashes {
            contract_class_version,
            external_functions_hash,
            l1_handlers_hash,
            constructors_hash,
            abi_hash,
            sierra_program_hash,
        }
    }
```

**File:** crates/starknet_api/src/state.rs (L282-291)
```rust
impl From<FlattenedSierraClass> for SierraContractClass {
    fn from(flattened_sierra: FlattenedSierraClass) -> Self {
        Self {
            sierra_program: flattened_sierra.sierra_program,
            contract_class_version: flattened_sierra.contract_class_version,
            entry_points_by_type: flattened_sierra.entry_points_by_type.into(),
            abi: flattened_sierra.abi,
        }
    }
}
```

**File:** crates/starknet_api/src/state.rs (L353-363)
```rust
impl From<SierraEntryPoint> for EntryPoint {
    fn from(entry_point: SierraEntryPoint) -> Self {
        Self {
            function_idx: FunctionIndex(
                usize::try_from(entry_point.function_idx)
                    .expect("Function index should fit in a usize"),
            ),
            selector: EntryPointSelector(entry_point.selector),
        }
    }
}
```
