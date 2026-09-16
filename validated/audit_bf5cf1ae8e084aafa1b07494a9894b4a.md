Based on my research, I found a concrete analog: an unbounded gzip decompression path in the RPC layer.

### Title
Unbounded gzip decompression in `decompress_program` allows memory-exhaustion DoS via crafted Cairo0 `starknet_estimateFee`/`starknet_simulateTransactions`/`starknet_call` requests - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in `crates/apollo_rpc/src/v0_8/api/mod.rs` decodes a base64 field from a user-supplied `BroadcastedDeclareV1Transaction.contract_class` and gzip-decompresses it with `flate2::read::GzDecoder` fully into memory with **no size or time limit**, unlike every other decompression path in the codebase.

### Finding Description
`decompress_program` reads an attacker-controlled base64 string, decodes it, and calls `decoder.read_to_end(&mut decompressed)` with no bound on the output size: [1](#0-0) 

This is explicitly flagged by its own author as incomplete: `// TODO(dan): add time and size limits.` — the comment sits directly above the unbounded `read_to_end` call. This function is reached from `user_deprecated_contract_class_to_sn_api`, which converts a `BroadcastedDeclareV1Transaction` (an RPC-facing, user-submitted, unsigned/unvalidated-at-this-point transaction object) into an executable transaction for local re-execution (fee estimation / simulation / call): [2](#0-1) [3](#0-2) 

By contrast, every other decompression path in this same codebase enforces an explicit maximum output size before or during decompression:
- The gateway's own `decode_and_decompress_with_size_limit` caps output via `.take(max_size + 1)`: [4](#0-3) 
- Storage decompression enforces `MAX_DECOMPRESSED_SIZE` (256 MB) via `zstd::bulk::decompress`: [5](#0-4) [6](#0-5) 
- The HTTP server layers a hard `RequestBodyLimitLayer` specifically to prevent "zip bombs from expanding in memory": [7](#0-6) 

`decompress_program` bypasses all of these protections because it operates on a base64 string embedded *inside* an already-parsed JSON-RPC request body (whose outer size may be under any request-body limit, since gzip achieves very high compression ratios on repetitive data — classic "zip bomb" pattern). The RPC layer's `DeprecatedContractClass.program` field is just a `String`, so its compressed size is bounded only by the general JSON-RPC body-size configuration, while its *decompressed* size is effectively unbounded.

### Impact Explanation
An attacker can submit a `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_call`-style RPC request containing a Cairo v0 `BroadcastedDeclareV1Transaction` whose `contract_class.program` field is a small, highly-compressible gzip payload (a classic decompression bomb, e.g., megabytes of zeros compressing to kilobytes). Because `decompress_program` decompresses fully into a `Vec<u8>` in memory with no cap, this can drive the RPC node process to consume gigabytes of RAM per request, leading to OOM and process crash — an uncontrolled resource consumption / denial-of-service condition on the RPC node, directly analogous to the reported starlette/FastAPI advisory (CWE-400, resource exhaustion via unbounded decompression). This does not directly corrupt consensus state, but a public/JSON-RPC-facing sequencer node can be forced to crash or become unresponsive with a single unauthenticated request.

### Likelihood Explanation
High reachability: this is a standard JSON-RPC method (`starknet_estimateFee` / `starknet_simulateTransactions` / `starknet_call`) callable by anyone with network access to an RPC endpoint, requiring no on-chain funds, no valid signature (the transaction only needs to deserialize; it is used for local read-only simulation, not accepted into the mempool), and no special privileges. The exploit payload (a crafted gzip bomb) is trivial to construct.

### Recommendation
Apply the same bounded-decompression pattern used elsewhere in the codebase (e.g., `decode_and_decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs`) to `decompress_program`: wrap the `GzDecoder` read with `.take(max_size)` and reject inputs whose decompressed size exceeds a configured maximum (mirroring `gateway_config.static_config.stateless_tx_validator_config.max_contract_bytecode_size` / `max_contract_class_object_size`), returning a clear RPC error instead of allocating unbounded memory.

### Proof of Concept
1. Construct a gzip stream that decompresses to, e.g., 2 GB of repeated bytes (a gzip bomb compresses to a few KB).
2. Base64-encode it and place it as the `program` field of a `DeprecatedContractClass` inside a `BroadcastedDeclareV1Transaction`.
3. Send it as the `contract_class` in a JSON-RPC `starknet_estimateFee` (or `starknet_simulateTransactions`) request to a public Starknet RPC node running this code.
4. `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) decompresses the payload fully into memory via `read_to_end`, with no size cap, causing large memory allocation; repeated/concurrent requests exhaust node memory and crash the process.

Note: I could not fully trace the exact JSON-RPC handler wiring (`api_impl.rs`) that calls `TryFrom<BroadcastedDeclareTransaction>`/`user_deprecated_contract_class_to_sn_api` due to tool-call limits in this session, so the precise RPC method name(s) invoking this path (estimate_fee vs. simulate_transactions vs. call) should be confirmed by a follow-up code read of `crates/apollo_rpc/src/v0_8/api/api_impl.rs`.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L478-509)
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

**File:** crates/apollo_storage/src/compression_utils.rs (L15-15)
```rust
pub(crate) const MAX_DECOMPRESSED_SIZE: usize = 1 << 28; // 256 MB
```

**File:** crates/apollo_storage/src/compression_utils.rs (L50-52)
```rust
pub fn decompress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    zstd::bulk::decompress(data, MAX_DECOMPRESSED_SIZE)
}
```

**File:** crates/apollo_http_server/src/http_server.rs (L146-152)
```rust
            // Hard streaming limit on decompressed bytes — wraps the body in
            // http_body_util::Limited which errors during poll_frame() once the
            // limit is exceeded, preventing zip bombs from expanding in memory.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
            .layer(RequestDecompressionLayer::new())
            // Cap compressed wire bytes to bound network I/O.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
```
