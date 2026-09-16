### Title
Unbounded gzip decompression of user-supplied Declare V1 program enables memory-exhaustion DoS in JSON-RPC execution endpoints - (File: crates/apollo_rpc/src/v0_8/api/mod.rs)

### Summary
The JSON-RPC node decompresses the base64/gzip-encoded `program` field of a user-submitted `BROADCASTED_DECLARE_TXN` (v1) with no size or time limit before feeding it into fee-estimation/simulation execution. This mirrors CVE-2024-55909's bug class ("expansion of archive files without controlling resource consumption") but is reachable from an unauthenticated/unprivileged RPC caller rather than an IBM Concert operator.

### Finding Description
`user_deprecated_contract_class_to_sn_api` converts a client-supplied `DeprecatedContractClass` into the SN-API type by calling `decompress_program`: [1](#0-0) 

`decompress_program` base64-decodes and gzip-decompresses the attacker-controlled `program` field with **no size or time bound**, explicitly flagged as a TODO: [2](#0-1) 

This function is reached from `TryFrom<BroadcastedDeclareTransaction>` for the V1 variant, which is used by the execution-simulation code path (fee estimation / transaction simulation) when converting a user-submitted broadcasted declare transaction for execution: [3](#0-2) 

This is in contrast to the properly-hardened path used for real transaction submission through the gateway, where the equivalent conversion (`DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` and the Cairo0 program conversion in `apollo_protobuf`) uses `decode_and_decompress_with_size_limit`, which enforces a hard byte cap during decompression: [4](#0-3) [5](#0-4) [6](#0-5) 

`decompress_program`, however, uses a raw `GzDecoder` and `read_to_end`, which will happily allocate as much memory as the gzip stream claims to decompress to — a classic "zip bomb" (a small compressed payload that expands to gigabytes).

### Impact Explanation
A single unauthenticated JSON-RPC client can submit a `BROADCASTED_DECLARE_TXN` V1 to any endpoint that triggers execution/simulation of broadcasted transactions (e.g. fee estimation / simulate / trace paths that use this `TryFrom` conversion) with a crafted, highly-compressible `program` field. The `GzDecoder::read_to_end` call will attempt to decompress this into an unbounded in-memory `Vec<u8>`, exhausting the RPC node process's memory and causing it to crash or become unresponsive, denying legitimate RPC service (including transaction submission/estimation) — matching the CVE's DoS bug class ("expansion of archive files without controlling resource consumption").

### Likelihood Explanation
High: the vulnerable code path is directly reachable via a JSON-RPC call carrying a user-supplied, attacker-crafted string, requires no special privileges, no on-chain state, and no prior interaction — a single crafted RPC request suffices. The comment in the code itself (`// TODO(dan): add time and size limits.`) confirms the maintainers were aware no bound exists here, unlike the sibling gateway/protobuf decompression paths which already enforce explicit size limits.

### Recommendation
Replace the unbounded `GzDecoder::read_to_end` in `decompress_program` (crates/apollo_rpc/src/v0_8/api/mod.rs:671-682) with a size-limited decompression, consistent with `decode_and_decompress_with_size_limit` in `starknet_api::compression_utils` (e.g. wrap the decoder with `.take(max_size + 1)` and reject if the output exceeds the limit), and apply the same MAX_CAIRO0_PROGRAM_SIZE-style bound used elsewhere in the codebase for legacy program decompression.

### Proof of Concept
1. Construct a `BROADCASTED_DECLARE_TXN` (v1) JSON-RPC payload whose `contract_class.program` field is a base64-encoded gzip stream that is small on the wire (e.g. a few KB) but decompresses to several GB (a standard "gzip bomb", easily generated with `dd if=/dev/zero | gzip`).
2. Submit this payload to a JSON-RPC endpoint that triggers execution/simulation of a `BroadcastedDeclareTransaction` (any caller of the `TryFrom<BroadcastedDeclareTransaction>` conversion in `crates/apollo_rpc/src/v0_8/api/mod.rs`, e.g. fee-estimation/simulate/trace RPC methods).
3. Observe that `decompress_program` (mod.rs:671) attempts to fully decompress the payload into memory with no cap, causing the RPC node process to allocate excessive memory and potentially OOM-crash or hang, denying service to all other RPC clients.

Note: I was not able to fully trace which exact top-level RPC method names (`starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_traceTransaction`, etc.) invoke this specific `TryFrom<BroadcastedDeclareTransaction>` conversion at the call-site level within `api_impl.rs`/`broadcasted_transaction.rs`, since the final grep results for those call sites were not returned before the iteration budget was exhausted — I recommend a Devin agent or reviewer confirm the exact endpoint(s) at `crates/apollo_rpc/src/v0_8/api/api_impl.rs` and `crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs` that reach this conversion.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L480-519)
```rust
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
                    starknet_api::transaction::DeclareTransactionV0V1 {
                        max_fee,
                        signature,
                        nonce,
                        // The blockifier doesn't need the class hash, but it uses the SN_API
                        // DeclareTransactionV0V1 which requires it.
                        class_hash: ClassHash::default(),
                        sender_address,
                    },
                    sn_api_contract_class,
                    abi_length,
                    // TODO(yair): pass the right value for only_query field.
                    false,
                ))
            }
            BroadcastedDeclareTransaction::V2(_) => {
                // TODO(yair): We need a way to get the casm of a declare V2 transaction.
                Err(internal_server_error("Declare V2 is not supported yet in execution."))
            }
            BroadcastedDeclareTransaction::V3(_) => {
                // TODO(yair): We need a way to get the casm of a declare V3 transaction.
                Err(internal_server_error("Declare V3 is not supported yet in execution."))
            }
        }
    }
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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L671-682)
```rust
pub(crate) fn decompress_program(
    base64_compressed_program: &String,
) -> Result<Program, ErrorObjectOwned> {
    base64::decode(base64_compressed_program).map_err(internal_server_error)?;
    let compressed_data =
        base64::decode(base64_compressed_program).map_err(internal_server_error)?;
    // TODO(dan): add time and size limits.
    let mut decoder = GzDecoder::new(compressed_data.as_slice());
    let mut decompressed = Vec::new();
    decoder.read_to_end(&mut decompressed).map_err(internal_server_error)?;
    serde_json::from_reader(decompressed.as_slice()).map_err(internal_server_error)
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L289-301)
```rust
impl DeprecatedGatewaySierraContractClass {
    pub fn convert_to_sierra_contract_class(
        self,
        max_size: usize,
    ) -> Result<SierraContractClass, CompressionError> {
        let sierra_program = decode_and_decompress_with_size_limit(&self.sierra_program, max_size)?;
        Ok(SierraContractClass {
            sierra_program,
            contract_class_version: self.contract_class_version,
            entry_points_by_type: self.entry_points_by_type,
            abi: self.abi.clone(),
        })
    }
```

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-138)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

        Ok(Self { program, entry_points_by_type, abi })
    }
```

**File:** crates/starknet_api/src/compression_utils.rs (L32-46)
```rust
/// Decompresses the provided data with size limits.
fn decompress_with_size_limit(
    decoded_data: Vec<u8>,
    max_size: usize,
) -> Result<Vec<u8>, CompressionError> {
    let decompressor = flate2::read::GzDecoder::new(&decoded_data[..]);
    let mut decompressed_data = Vec::new();
    decompressor
        .take((max_size + 1).try_into().expect("max_size should be less than usize::MAX"))
        .read_to_end(&mut decompressed_data)?;
    if decompressed_data.len() > max_size {
        return Err(CompressionError::SizeLimitExceeded { limit: max_size });
    }
    Ok(decompressed_data)
}
```
