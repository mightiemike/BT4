### Title
Unbounded gzip decompression of user-supplied `compressed_program` in deprecated declare transaction path enables memory-exhaustion DoS - (File: crates/apollo_rpc/src/v0_8/api/mod.rs)

### Summary
`decompress_program` in `crates/apollo_rpc/src/v0_8/api/mod.rs` decompresses an attacker-controlled, base64+gzip-encoded `compressed_program` field with **no size limit**, unlike the sibling utility `starknet_api::compression_utils::decode_and_decompress_with_size_limit` used elsewhere in the codebase, which explicitly truncates decompression at a configured `max_size`. This is the same bug class as GHSA-cgqf-3cq5-wvcj / CVE-2024-28101 (Apollo Router decompressing payloads before enforcing size limits): decompression happens fully into memory before any bound is applied.

### Finding Description
`decompress_program` reads the entirety of a gzip stream into an unbounded `Vec<u8>`: [1](#0-0) 

The function is invoked from `user_deprecated_contract_class_to_sn_api`, which converts a user-submitted deprecated contract class (containing an attacker-supplied `compressed_program` string) into the internal `starknet_api::deprecated_contract_class::ContractClass` representation: [2](#0-1) 

The code even carries an explicit acknowledgment of the missing protection: `// TODO(dan): add time and size limits.` This is in stark contrast to the analogous, already-hardened path for Sierra/legacy programs elsewhere in the codebase, which enforces a hard cap during decompression itself (not just after): [3](#0-2) 

The rest of the codebase treats this class of bug as serious enough to defend in depth (e.g., the HTTP gateway wraps `RequestDecompressionLayer` with a hard streaming `RequestBodyLimitLayer` specifically to stop "zip bombs [from] expand[ing] in memory," and `apollo_storage`'s serializers warn/limit against `MAX_DECOMPRESSED_SIZE`), but `decompress_program`'s call path was missed.

### Impact Explanation
An attacker submitting a deprecated (Cairo0) declare transaction (or any request path that constructs a `DeprecatedContractClass` from client/writer objects) can supply a small, highly-compressed gzip blob as `compressed_program`. `GzDecoder::read_to_end` will decompress it fully into memory with no size cap, allowing a small request (KBs) to expand to an enormous allocation (potentially GBs, limited only by the gzip compression ratio achievable, ~1000:1+ for repetitive data). This can exhaust node memory, causing the RPC/full node process to crash or become unresponsive — a Denial-of-Service against the node servicing this RPC/transaction path, matching the "network unable to confirm new transactions" impact criterion when it affects sequencer-adjacent nodes handling this API.

### Likelihood Explanation
High likelihood: no privileged access, special key, or non-standard interaction is required. Any party able to reach the deprecated Declare transaction conversion path (via `user_deprecated_contract_class_to_sn_api`) with a crafted, small, highly-compressed `compressed_program` string can trigger it. The vulnerable code has zero size/time guard, and the TODO comment confirms it was a known, unaddressed gap.

### Recommendation
Replace the manual `GzDecoder` usage in `decompress_program` with a bounded reader, mirroring the pattern already used by `starknet_api::compression_utils::decompress_with_size_limit` — i.e., wrap the decoder in `.take(max_size + 1)` and reject the result if it exceeds `max_size`, returning an RPC error instead of allocating unbounded memory. Ideally, replace the bespoke decompression logic with a call into the existing, size-limited `decode_and_decompress_with_size_limit` utility to avoid duplicated/divergent implementations.

### Proof of Concept
1. Craft a gzip-compressed payload of e.g. 10 GB of a repeated byte (`vec![b'a'; 10 * 1024 * 1024 * 1024]`), which compresses down to a few KB.
2. Base64-encode the compressed bytes and place them into the `compressed_program` field of a deprecated Declare transaction object that is deserialized through `user_deprecated_contract_class_to_sn_api` (e.g., via the write/feeder-gateway conversion path that handles Cairo0 declared classes).
3. Submit the transaction/object to the node. `decompress_program` calls `decoder.read_to_end(&mut decompressed)` with no size bound, causing the process to attempt a multi-GB allocation from a few-KB request, exhausting memory and crashing or hanging the node — analogous to the `zstd_decompressed_request_too_large`/zip-bomb test scenario already guarded against elsewhere in the codebase (`crates/apollo_http_server/src/http_server_test.rs:456-477`) but missing here.

### Citations

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
