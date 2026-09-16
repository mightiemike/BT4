Based on my investigation, I found a plausible analog: an `.expect()`-based panic reachable from an attacker-declared Sierra class, in the entry-point conversion path.

### Title
Panic-inducing `.expect()` on attacker-controlled `function_idx` during Sierra class conversion - ([File: crates/starknet_api/src/state.rs])

### Summary
`impl From<SierraEntryPoint> for EntryPoint` in `starknet_api::state` converts a Sierra contract class's `function_idx` (a raw integer from `cairo_lang_starknet_classes`) into a `usize` using `usize::try_from(...).expect("Function index should fit in a usize")` [1](#0-0) . This value originates from a `Declare` transaction's Sierra `contract_class.entry_points_by_type`, a field fully controlled by any unprivileged declarer, per the OpenRPC schema which types `function_idx` simply as `"type": "integer"` with no explicit upper bound [2](#0-1) . Similar `.expect()`-based panics on attacker-supplied indices/offsets also appear in the p2p protobuf conversion path (`protobuf::SierraEntryPoint` → `state::EntryPoint` and `protobuf::EntryPoint` → deprecated `EntryPointV0`) [3](#0-2) .

### Finding Description
The CVE-2018-1318 bug class is: a carefully crafted, unprivileged client request reaches an unguarded/unchecked code path and causes the serving process to crash (segfault in ATS; here, a Rust `panic!`/abort via `.expect()`). In this sequencer codebase, `StatelessTransactionValidator::validate_declare_tx` only checks Sierra version, class length, and that entry points are sorted/unique [4](#0-3) ; it does not validate that each `function_idx` fits into `usize` before the class is later converted via `From<SierraEntryPoint> for EntryPoint`, which unconditionally calls `.expect(...)` on `usize::try_from(entry_point.function_idx)` [1](#0-0) . On a 32-bit build (or any platform where `usize` is narrower than the underlying integer type used for `function_idx`), or if the value exceeds `usize::MAX` on that platform, this conversion fails and the `.expect()` panics the calling thread. Because this conversion is invoked while processing declared contract classes on paths reachable from gateway ingestion, mempool P2P propagation, and downstream execution (class hashing, compilation utilities), a single malicious `Declare` transaction (or propagated declared class) whose Sierra JSON specifies an out-of-range `function_idx` can trigger this panic before/without full validation rejecting it.

### Impact Explanation
If the panic occurs on a thread that is not caught/isolated (e.g., a per-connection async task without `catch_unwind`, or a thread that is part of a `tokio` worker pool without panic isolation), the panic can abort the process or poison shared state (e.g., a `Mutex` guarding mempool/gateway state), causing denial of service to a node — analogous to the segfault in the ATS CVE, which is also a crash-on-crafted-request bug. This matches the "no-privilege attacker triggers node crash" bug class from the report and could result in the affected node being unable to confirm new transactions if enough validators hit this path (a Medium/High-severity impact per the report scope).

### Likelihood Explanation
Likelihood is uncertain without runtime verification. I could not confirm from static reading alone (a) whether `usize` is 64-bit-only in all deployed environments (removing exploitability on typical 64-bit servers, since `function_idx` would need to exceed `u64::MAX` when deserialized from JSON, which is unlikely given how the value is typically parsed as `u64`/`usize` already at deserialization), and (b) whether the panic is caught/isolated by an outer `catch_unwind` or supervisor restart logic in the gateway/mempool call stack, which would reduce impact to a per-request failure rather than a full crash. Given the JSON schema types `function_idx` merely as `"integer"` with no bound and I did not find explicit range validation of `function_idx` prior to this conversion in `apollo_gateway`, there is a plausible but not fully confirmed path from a malicious `Declare` payload to this `.expect()`.

### Recommendation
Replace the `.expect("Function index should fit in a usize")` panics in `From<SierraEntryPoint> for EntryPoint` (`crates/starknet_api/src/state.rs`) and the analogous protobuf conversions in `crates/apollo_protobuf/src/converters/class.rs` with fallible `TryFrom` conversions that return a validation error instead of panicking, and add explicit bounds validation for `function_idx` (and `offset`) in `StatelessTransactionValidator::validate_declare_tx` before any class-processing pipeline consumes it.

### Proof of Concept
Not independently reproduced — I was unable to execute code or confirm at what integer width `function_idx` is deserialized in the JSON/RPC pipeline, nor whether an outer panic-catcher exists in the calling stack (gateway/mempool/p2p handlers). This is a conceptual PoC: submit (or propagate over p2p) a `Declare` v3 transaction whose `contract_class.entry_points_by_type` contains an entry point with `function_idx` set to a value exceeding `usize::MAX` on the target's platform (relevant mainly for 32-bit builds) or otherwise incompatible with the `usize::try_from` conversion, then observe whether the receiving node's thread panics when the `SierraContractClass` → `EntryPoint` conversion executes.

**Confidence caveat:** This finding is based on static code reading only. I could not verify the exact deserialization type used for `function_idx` on the JSON ingestion path (which determines whether an out-of-range value is even representable before reaching this `.expect()`), nor confirm the presence/absence of panic-catching around the call sites. I recommend a Devin session with full build/test access to trace the exact type flow from RPC JSON → `SierraEntryPoint` → this conversion, and to attempt a live reproduction, before treating this as a confirmed vulnerability rather than a plausible analog.

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

**File:** crates/apollo_rpc/resources/V0_8/starknet_api_openrpc.json (L3388-3407)
```json
            "SIERRA_ENTRY_POINT": {
                "title": "Sierra entry point",
                "type": "object",
                "properties": {
                    "selector": {
                        "title": "Selector",
                        "description": "A unique identifier of the entry point (function) in the program",
                        "$ref": "#/components/schemas/FELT"
                    },
                    "function_idx": {
                        "title": "Function index",
                        "description": "The index of the function in the program",
                        "type": "integer"
                    }
                },
                "required": [
                    "selector",
                    "function_idx"
                ]
            },
```

**File:** crates/apollo_protobuf/src/converters/class.rs (L292-327)
```rust
impl TryFrom<protobuf::EntryPoint> for deprecated_contract_class::EntryPointV0 {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::EntryPoint) -> Result<Self, Self::Error> {
        let selector_felt = Felt::try_from(value.selector.ok_or(missing("EntryPoint::selector"))?)?;
        let selector = EntryPointSelector(selector_felt);

        let offset = deprecated_contract_class::EntryPointOffset(
            value.offset.try_into().expect("Failed converting u64 to usize"),
        );

        Ok(deprecated_contract_class::EntryPointV0 { selector, offset })
    }
}

impl From<deprecated_contract_class::EntryPointV0> for protobuf::EntryPoint {
    fn from(value: deprecated_contract_class::EntryPointV0) -> Self {
        protobuf::EntryPoint {
            selector: Some(value.selector.0.into()),
            offset: u64::try_from(value.offset.0).expect("Failed converting usize to u64"),
        }
    }
}

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L280-291)
```rust
    fn validate_declare_tx(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let contract_class = match declare_tx {
            RpcDeclareTransaction::V3(tx) => &tx.contract_class,
        };
        self.validate_sierra_version(&contract_class.sierra_program)?;
        self.validate_class_length(contract_class)?;
        self.validate_entry_points_sorted_and_unique(contract_class)?;
        Ok(())
    }
```
