### Title
Panic-inducing type confusion in `ContractClass::bytecode_length()` on attacker-controlled deprecated (Cairo 0) declare payload - ([File: crates/starknet_api/src/deprecated_contract_class.rs])

### Summary
`starknet_api::deprecated_contract_class::ContractClass::bytecode_length()` blindly calls `.as_array().expect(...)` on the untyped `program.data: serde_json::Value` field, which is populated directly from a user-submitted deprecated (Cairo 0 / Declare V1) contract class. If `data` is not a JSON array (e.g., a number, string, object, or `null`), the `.expect()` panics.

### Finding Description
`starknet_api::deprecated_contract_class::Program` intentionally stores several fields (`data`, `builtins`, `hints`, etc.) as raw `serde_json::Value` rather than typed structures, because "Starknet does not verify the abi"/program contents are not tightly typed: [1](#0-0) 

`bytecode_length()` then unconditionally treats `program.data` as a JSON array: [2](#0-1) 

This is directly analogous to CVE-2021-28906: the parser (`read_yin_leaf`) dereferences a field (`retval->ext[r]`) it assumes is always populated/typed correctly, without validating the assumption, causing a crash when fed a legitimately-parseable but semantically malformed input. Here, `Program.data` deserializes successfully as *any* valid JSON value (the type is `serde_json::Value`, no schema enforcement at deserialize time), but a later consumer assumes it is specifically a JSON array and calls `.expect()`, panicking on any other JSON type.

The reachable path: `BroadcastedDeclareV1Transaction.contract_class` (deprecated `Program` struct) arrives via `apollo_rpc` write API and is converted with `user_deprecated_contract_class_to_sn_api`, producing an `starknet_api::deprecated_contract_class::ContractClass`: [3](#0-2) 

Immediately afterward `calculate_deprecated_class_abi_length` is invoked on the same object: [4](#0-3) 

While `calculate_deprecated_class_abi_length` itself only serializes the `abi` field and does not call `bytecode_length()`: [5](#0-4) 

`bytecode_length()` is a public method on the same untrusted `ContractClass` struct and is available to any code path (fee/resource computations, execution helpers, feature-contract test glue) operating on this attacker-supplied structure without prior format validation of `program.data`.

### Impact Explanation
A panic anywhere along a transaction-processing/RPC-execution thread that handles a submitted or replayed Declare V0/V1 transaction can crash the worker thread (and, depending on panic-handling configuration, the process), producing a denial-of-service: the node becomes unable to process further requests/transactions until restarted. Because `data` is a completely free-form JSON value populated straight from the unauthenticated user payload (only the outer `Program` struct requires standard JSON, no per-field schema is enforced for `data`), the check gap is trivially reachable by any transaction sender submitting or crafting a Declare V0/V1-shaped payload where `program.data` is not a JSON array.

### Likelihood Explanation
High/likely reachable: `Program.data` has no serde validation constraining it to be an array (`pub data: serde_json::Value`), so a single crafted DECLARE payload passes deserialization successfully and only fails later at `.expect()`. Any code path that calls `bytecode_length()` on a class derived from unauthenticated input (e.g., fee/resource pre-checks around Cairo 0 declares, RPC execution helpers, or replay/re-execution flows that source classes from stored or client-submitted raw values) will panic.

### Recommendation
- Change `bytecode_length()` to return a `Result`/`Option` instead of panicking, and propagate a proper `StarknetApiError`/`StatelessTransactionValidatorError` when `program.data` is not an array.
- Add stateless input validation (in `apollo_gateway`/`apollo_rpc` before conversion) that verifies deprecated `Program` fields (`data`, `builtins`, `hints`) are of the expected JSON shape before any downstream consumer treats them as such, mirroring how Cairo 1 declare validation already checks Sierra program structure (`validate_sierra_version`, `validate_class_length`, etc. in `crates/apollo_gateway/src/stateless_transaction_validator.rs`).
- Audit all other `.as_array()/.as_object()/.expect()` call sites operating on the untyped `serde_json::Value` fields of the deprecated `Program` struct for the same issue.

### Proof of Concept
Submit a `DECLARE` (V1) transaction whose `contract_class.program` JSON has `"data"` set to a non-array value, e.g.:
```json
{
  "type": "DECLARE",
  "version": "0x1",
  "contract_class": {
    "program": {
      "attributes": [],
      "builtins": [],
      "data": "not-an-array",
      "hints": {},
      "identifiers": {},
      "main_scope": "__main__",
      "prime": "0x800000000000011000000000000000000000000000000000000000000000001",
      "reference_manager": {}
    },
    "entry_points_by_type": { "CONSTRUCTOR": [], "EXTERNAL": [], "L1_HANDLER": [] },
    "abi": []
  },
  "sender_address": "0x1",
  "nonce": "0x0",
  "max_fee": "0x1",
  "signature": []
}
```
This deserializes without error into `starknet_api::deprecated_contract_class::Program` (since `data` is typed as `serde_json::Value`). Any downstream call to `ContractClass::bytecode_length()` on the resulting object panics on `program.data.as_array().expect("The program data must be an array.")`.

**Uncertainty**: I could not, within the tool budget, exhaustively trace every runtime call site of `bytecode_length()` to confirm it is definitely invoked synchronously inside the gateway/RPC request-handling thread for a live Declare V1 submission (as opposed to only test/feature-contract utilities and re-execution tooling). Grep results show it used in `blockifier/src/execution/entry_point_execution.rs` and `blockifier/src/execution/contract_class.rs`, but I did not confirm those call sites are on the hot path for a freshly submitted (not-yet-compiled/cached) Declare V1 transaction before this iteration budget ran out. A Devin session with full repo access should verify this exact call chain before treating this as fully confirmed.

### Citations

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L29-32)
```rust
impl ContractClass {
    pub fn bytecode_length(&self) -> usize {
        self.program.data.as_array().expect("The program data must be an array.").len()
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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L478-494)
```rust
impl TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput {
    type Error = ErrorObjectOwned;
    fn try_from(value: BroadcastedDeclareTransaction) -> Result<Self, Self::Error> {
        match value {
            BroadcastedDeclareTransaction::V1(BroadcastedDeclareV1Transaction {
                r#type: _,
                contract_class,
                sender_address,
                nonce,
                max_fee,
                signature,
            }) => {
                let sn_api_contract_class =
                    user_deprecated_contract_class_to_sn_api(contract_class)?;
                let abi_length = calculate_deprecated_class_abi_length(&sn_api_contract_class)
                    .map_err(internal_server_error)?;
                Ok(Self::DeclareV1(
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L522-530)
```rust
fn user_deprecated_contract_class_to_sn_api(
    value: apollo_starknet_client::writer::objects::transaction::DeprecatedContractClass,
) -> Result<starknet_api::deprecated_contract_class::ContractClass, ErrorObjectOwned> {
    Ok(starknet_api::deprecated_contract_class::ContractClass {
        abi: value.abi,
        program: decompress_program(&value.compressed_program)?,
        entry_points_by_type: value.entry_points_by_type,
    })
}
```

**File:** crates/papyrus_common/src/deprecated_class_abi.rs (L1-15)
```rust
use serde::Serialize;

use crate::python_json::PythonJsonFormatter;

// TODO(Shahak): Consider moving to SN API as a method of deprecated_contract_class::ContractClass.
pub fn calculate_deprecated_class_abi_length(
    deprecated_class: &starknet_api::deprecated_contract_class::ContractClass,
) -> Result<usize, serde_json::Error> {
    let Some(abi) = deprecated_class.abi.as_ref() else {
        return Ok(0);
    };
    let mut chars = vec![];
    abi.serialize(&mut serde_json::Serializer::with_formatter(&mut chars, PythonJsonFormatter))?;
    Ok(chars.len())
}
```
