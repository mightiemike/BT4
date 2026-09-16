### Title
Unbounded gzip decompression of user-supplied `program` in `decompress_program` allows CPU/memory exhaustion DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in `crates/apollo_rpc/src/v0_8/api/mod.rs` decodes a base64 string and feeds it into `flate2::bufread::GzDecoder`, then reads it to completion with `read_to_end` and no size/time limit, unlike the equivalent decompression helpers elsewhere in the codebase which explicitly enforce a maximum decompressed size.

### Finding Description
The function:
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
``` [1](#0-0) 

is called on a `program` field that is decoded from a `BroadcastedDeclareV1Transaction` submitted for simulation/fee-estimation. This is the same bug class as CVE-2015-5312 (uncontrolled resource consumption via unbounded decompression/expansion of attacker-supplied data): a small, cheaply-transmitted compressed payload (a gzip "bomb") can expand to an enormous size in memory, consuming CPU and RAM without any pre-check.

This directly contrasts with the pattern the codebase itself uses elsewhere for exactly this kind of input, e.g.:
- `crates/starknet_api/src/compression_utils.rs` bounds decompression with `decompress_with_size_limit`, explicitly capping output size and erroring with `CompressionError::SizeLimitExceeded` if the limit is exceeded. [2](#0-1) 
- `crates/apollo_storage/src/compression_utils.rs` similarly enforces `MAX_DECOMPRESSED_SIZE` (256 MB) via `zstd::bulk::decompress`. [3](#0-2) 

The TODO comment in `decompress_program` (`// TODO(dan): add time and size limits.`) is an explicit acknowledgment by the codebase authors that this exact bound is missing here.

### Impact Explanation
An unbounded decompression triggered by attacker-controlled, unauthenticated input (a submitted Declare V1 transaction payload used for fee estimation/simulation) allows a single request to force the node to allocate an unbounded amount of memory and CPU decompressing a gzip bomb, which can crash or freeze the RPC/execution process handling that request, degrading availability of that service. This matches the "network unable to confirm new transactions" / DoS impact class if the affected RPC path is shared with or blocks other request processing.

### Likelihood Explanation
I could not fully confirm, within the tool budget available, the exact RPC method(s) (e.g., `estimate_fee`, `simulate_transaction`, `add_declare_transaction`) that route a `BroadcastedDeclareV1Transaction`'s `program` field into `decompress_program`, nor whether this code path is reachable pre-authentication from any RPC client without rate limiting. The two call sites found are in `crates/apollo_rpc/src/v0_8/api/mod.rs` itself and its test file `crates/apollo_rpc/src/v0_8/execution_test.rs`; I was unable to trace the full call chain from the public RPC trait methods before running out of iterations.

### Recommendation
Add explicit size (and ideally time) limits to `decompress_program`, mirroring the pattern already used in `starknet_api::compression_utils::decompress_with_size_limit` and `apollo_storage::compression_utils::decompress` (e.g., wrap the `GzDecoder` read with a `.take(MAX_SIZE)` and reject payloads exceeding the bound, returning an RPC error instead of unbounded allocation).

### Proof of Concept
Not independently verified end-to-end due to tool/iteration limits — I was unable to confirm the exact reachable RPC entry point in this session. **This finding should be treated as unconfirmed/needs verification**: a Devin session with full repo/tooling access should trace which public JSON-RPC method(s) invoke `decompress_program` with attacker-controlled `program` data (search `BroadcastedDeclareV1Transaction`/`DeclareV0`/`Program` usage across `crates/apollo_rpc/src/v0_8/`), and, if confirmed reachable without pre-validation/size caps, craft a base64-encoded gzip payload with a very high compression ratio (e.g., a large run of zero bytes) and submit it via that RPC method to observe memory/CPU consumption.

### Citations

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

**File:** crates/apollo_storage/src/compression_utils.rs (L12-51)
```rust
// The maximum size of the decompressed data.
// TODO(Dvir): consider defining this for each type separately and pass it as an argument to the
// decompress function.
pub(crate) const MAX_DECOMPRESSED_SIZE: usize = 1 << 28; // 256 MB
// The compression level to use. Higher levels are slower but compress better.
const COMPRESSION_LEVEL: i32 = zstd::DEFAULT_COMPRESSION_LEVEL;

/// Returns the compressed data in a vector.
///
/// # Arguments
/// * data - bytes to compress.
///
/// # Errors
/// Returns [`std::io::Error`] if any read error is encountered.
pub fn compress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    zstd::bulk::compress(data, COMPRESSION_LEVEL)
}

/// Serialized and then compress object.
///
/// # Arguments
/// * object - the object to serialize and compress.
///
/// # Errors
/// Returns [`StorageSerdeError`] if any error is encountered in the serialization or compression.
pub fn serialize_and_compress(object: &impl StorageSerde) -> Result<Vec<u8>, StorageSerdeError> {
    let mut buf = Vec::new();
    object.serialize_into(&mut buf)?;
    Ok(compress(buf.as_slice())?)
}

/// Decompress data and returns it as bytes in a vector.
///
/// # Arguments
/// * data - bytes to decompress.
///
/// # Errors
/// Returns [`std::io::Error`] if any read error is encountered.
pub fn decompress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    zstd::bulk::decompress(data, MAX_DECOMPRESSED_SIZE)
```
