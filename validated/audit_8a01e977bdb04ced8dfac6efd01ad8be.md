### Title
Unbounded gzip decompression of user-supplied `contract_class.program` in `decompress_program` enables a memory-exhaustion DoS via RPC/gateway declare-v1/trace paths - (File: crates/apollo_rpc/src/v0_8/api/mod.rs)

### Summary
`apollo_rpc`'s `decompress_program` function decompresses a base64-encoded gzip blob supplied directly by an RPC caller (as part of a `DeprecatedContractClass`/`BroadcastedDeclareV1Transaction.contract_class.program` field, used in `trace`/`simulate`/legacy declare flows) without any bound on the decompressed size or time, exactly matching the httplib2 decompression-bomb bug class (unbounded `GzDecoder::read_to_end`).

### Finding Description
```rust
// crates/apollo_rpc/src/v0_8/api/mod.rs:671-682
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
The `// TODO(dan): add time and size limits.` comment self-documents the missing bound. `decoder.read_to_end(&mut decompressed)` performs unbounded, in-memory gzip decompression identical in structure to the vulnerable `gzip.GzipFile(...).read()` call flagged in the httplib2 advisory: a small compressed input can inflate to an arbitrarily large `Vec<u8>` before any size check occurs.

By contrast, the sequencer's own `starknet_api::compression_utils::decode_and_decompress_with_size_limit` (`crates/starknet_api/src/compression_utils.rs:33-46`) correctly uses `decompressor.take(max_size + 1)` to bound decompression output, and `apollo_storage::compression_utils::decompress` (`crates/apollo_storage/src/compression_utils.rs:50-51`) bounds it via `zstd::bulk::decompress(data, MAX_DECOMPRESSED_SIZE)`. `decompress_program` was not updated to use either pattern, leaving it as an outlier unbounded decompression path directly reachable from client input.

This is the base64+gzip payload of a `program` field inside a `DeprecatedContractClass`/`BroadcastedDeclareV1Transaction`, which any unprivileged JSON-RPC caller can submit through `trace_transaction`/`simulate_transactions`-style entry points that accept a broadcasted declare-v1 transaction for local re-execution/estimation by an RPC node. An attacker submits a request containing a small (KB-scale) gzip blob that decompresses to hundreds of MB or more, causing the node process to allocate an oversized `Vec<u8>` before any JSON parsing or contract-class validation occurs.

### Impact Explanation
An attacker-controlled RPC request can force the target node (feeder/RPC-serving sequencer component) to allocate arbitrarily large memory for the decompressed payload before any size/version validation, potentially exhausting node memory / crashing the process serving RPC requests (`MemoryError`/OOM). This is a Medium/High-severity resource-exhaustion issue rather than a state-corruption bug, but it can cause a node to become unable to serve RPC requests (network unable to confirm/serve new transactions through that node) if the affected process is shared with critical duties.

### Likelihood Explanation
Likelihood is high for triggering the code path (single unauthenticated JSON-RPC request, no special privileges, no state assumptions) since it only requires calling an RPC method that accepts a `BroadcastedDeclareV1Transaction`/`DeprecatedContractClass` and passes its `program` field through `decompress_program`. The compression ratio achievable with gzip (>1000x) makes a devastating memory footprint trivial to construct, mirroring the httplib2 PoC exactly.

### Recommendation
Apply the existing, already-used size-limiting pattern in the codebase (`decode_and_decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs`) to `decompress_program`: wrap the `GzDecoder` reader with `.take(max_size + 1)` and reject/short-circuit once the configured maximum decompressed program size is exceeded, before allocating unbounded memory. Address the `// TODO(dan): add time and size limits.` comment by adding both a maximum decompressed-size bound and (ideally) a decompression time budget, consistent with the `MAX_DECOMPRESSED_SIZE` bound already enforced elsewhere in the codebase (`crates/apollo_storage/src/compression_utils.rs:15`).

### Proof of Concept
1. Construct a `program` string that is a base64-encoded gzip blob of, e.g., 1 MB of repeated bytes, which decompresses to several hundred MB (analogous to the httplib2 PoC: `gzip.compress(b"A" * 300_000_000)` produces a payload of a few hundred KB).
2. Submit a JSON-RPC request (e.g., `trace_transaction`/`simulate_transactions`, or the legacy declare-v1 gateway path) containing a `BroadcastedDeclareV1Transaction`/`DeprecatedContractClass` whose `contract_class.program` field is set to this base64 string.
3. The RPC/execution layer invokes `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671`), which calls `decoder.read_to_end(&mut decompressed)` with no size cap, allocating hundreds of MB from a request whose wire size is only a few hundred KB, before any contract-class validation rejects it.
4. Repeating this with multiple concurrent requests, or a single sufficiently large decompression ratio, exhausts node memory / crashes the RPC-serving process. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_storage/src/compression_utils.rs (L12-17)
```rust
// The maximum size of the decompressed data.
// TODO(Dvir): consider defining this for each type separately and pass it as an argument to the
// decompress function.
pub(crate) const MAX_DECOMPRESSED_SIZE: usize = 1 << 28; // 256 MB
// The compression level to use. Higher levels are slower but compress better.
const COMPRESSION_LEVEL: i32 = zstd::DEFAULT_COMPRESSION_LEVEL;
```

**File:** crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs (L70-79)
```rust
#[derive(Debug, Default, Deserialize, Serialize, Clone, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BroadcastedDeclareV1Transaction {
    pub r#type: DeclareType,
    pub contract_class: DeprecatedContractClass,
    pub sender_address: ContractAddress,
    pub nonce: Nonce,
    pub max_fee: Fee,
    pub signature: TransactionSignature,
}
```
