### Title
Panic on Sierra Declare Transaction with Out-of-Range `function_idx` Causes Sequencer Denial of Service - (File: `crates/starknet_api/src/state.rs`)

### Summary
The Sierra contract class deserialization path contains an `.expect()` call that panics when a declared class's `function_idx` field (an untrusted, attacker-controlled `Felt`/integer from a submitted Declare transaction) does not fit into a `usize`. This is directly analogous to CVE-2018-17231, where unvalidated, attacker-influenced input triggers an "index out of range"-style assertion failure that crashes the running process (denial of service), rather than being gracefully rejected as invalid input.

### Finding Description
`SierraContractClass` entry points are represented by `EntryPoint { function_idx: FunctionIndex(usize), selector }`. The conversion from the wire/protobuf representation `SierraEntryPoint` (where `function_idx` is a wider integer type, not bounds-checked against `usize`) uses `.expect()` instead of returning a `Result`: [1](#0-0) 

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

This mirrors a nearly identical pattern in the protobuf conversion path used for L1/L2 message and class propagation: [2](#0-1) 

An attacker submitting a Declare transaction (via the gateway RPC) can construct a Sierra contract class whose `entry_points_by_type` contains a `function_idx` value that is syntactically valid per the RPC schema (`"type": "integer"`, no explicit upper bound tied to `usize` in `apollo_rpc/resources/V0_8/starknet_api_openrpc.json`) but numerically exceeds `usize::MAX` on the executing platform, or otherwise fails `TryFrom` conversion. When such a class reaches the `From<SierraEntryPoint> for EntryPoint` conversion (or the equivalent protobuf conversion during class propagation/sync), the `.expect()` panics instead of returning a validation error, unwinding/aborting the process that performs the conversion.

Unlike the well-guarded `SierraVersion::extract_from_program` function, which validates program length and gracefully maps parse failures to `StarknetApiError::ParseSierraVersionError` (see `crates/starknet_api/src/contract_class/structs.rs:91-119`), and unlike `VersionId::from_sierra_program` which safely uses `.get(..6)` with a `Result` error path (`crates/apollo_gateway_config/src/compiler_version.rs:37-56`), the `EntryPoint` conversions above have no equivalent bounds validation prior to the panicking conversion.

### Impact Explanation
A panic triggered by processing a single unprivileged, syntactically valid Declare transaction (or a propagated class message) can crash the process executing the conversion — for example gateway/class-conversion or sync/class-propagation components. If not caught at a task boundary, this results in denial of service: the affected component stops processing new transactions/classes, potentially halting confirmation of new transactions network-wide if reproduced across honest nodes (since it is a deterministic, input-dependent panic, all nodes performing the same conversion on the same malicious class would crash identically).

### Likelihood Explanation
Likelihood depends on: (1) whether the RPC/API layer for Declare transactions enforces a `usize`-safe range on `function_idx` before this conversion is reached, and (2) whether the conversion is wrapped by a panic-catching boundary (e.g., `catch_unwind` around per-request handling) in the actual deployed service. I was not able to confirm from the index whether an upstream validator rejects out-of-range `function_idx` before reaching `From<SierraEntryPoint> for EntryPoint`, nor whether panics in this call path are isolated per-request or crash the whole process. This uncertainty means the concrete exploitability and blast radius (single request failure vs. full process crash) could not be fully confirmed with the tools available.

### Recommendation
- Replace `.expect("Function index should fit in a usize")` in `crates/starknet_api/src/state.rs` (`From<SierraEntryPoint> for EntryPoint`) with a fallible `TryFrom` implementation that returns a `StarknetApiError`/validation error, mirroring the pattern used in `SierraVersion::extract_from_program`.
- Apply the same fix to the analogous protobuf conversion in `crates/apollo_protobuf/src/converters/class.rs:315-327` (and the `EntryPointV0`/`offset` conversions nearby, which use similar `.expect()` calls).
- Add stateless validation in the gateway's declare-transaction path that rejects `function_idx` values outside the valid `usize` range before any conversion occurs.

### Proof of Concept
Not independently reproducible from static analysis alone — reproduction requires confirming (a) the exact upstream call boundary that invokes `From<SierraEntryPoint> for EntryPoint` on attacker-supplied Declare transaction data, and (b) that no prior schema/stateless validation rejects out-of-range `function_idx`. Conceptually: submit a Declare transaction (or Sierra class propagation message) whose `entry_points_by_type` contains an entry with `function_idx` set to a value exceeding `usize::MAX` (e.g., `u64::MAX` or larger, depending on the wire type), and observe whether the receiving component panics during class conversion rather than returning a structured validation error.

### Citations

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

**File:** crates/apollo_protobuf/src/converters/class.rs (L315-327)
```rust
impl TryFrom<protobuf::SierraEntryPoint> for state::EntryPoint {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::SierraEntryPoint) -> Result<Self, Self::Error> {
        let selector_felt =
            Felt::try_from(value.selector.ok_or(missing("SierraEntryPoint::selector"))?)?;
        let selector = EntryPointSelector(selector_felt);

        let function_idx =
            state::FunctionIndex(value.index.try_into().expect("Failed converting u64 to usize"));

        Ok(state::EntryPoint { function_idx, selector })
    }
}
```
