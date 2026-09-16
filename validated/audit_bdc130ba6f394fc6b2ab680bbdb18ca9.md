I found a valid analog. The `decompress_program` function in `apollo_rpc` decompresses a gzip payload from an **unprivileged RPC caller's `simulate_transactions` request** with no size limit — directly analogous to the CVE's unbounded/unchecked length-based read from untrusted data.

### Title
Unbounded gzip decompression of user-supplied declare-v1 program in `simulate_transactions` enables single-request DoS - (File: `crates/apollo_rpc/src/v0_8/api/mod.rs`)

### Summary
`decompress_program` decodes a base64 string and gzip-decompresses it into an in-memory `Vec<u8>` with **no size or time limit**, unlike the sibling function `decode_and_decompress_with_size_limit` in `starknet_api::compression_utils`, which properly bounds decompression output. [1](#0-0)  This is the same bug class as CVE-2025-66960: untrusted, attacker-controlled length/size information is used to drive an unbounded read/allocation before any validation, causing resource exhaustion or a crash.

### Finding Description
`decompress_program` is reachable from `simulate_transactions`, a JSON-RPC method that accepts a `Vec<BroadcastedTransaction>` directly from any caller. [2](#0-1)  When a `BroadcastedDeclareTransaction::V1` is supplied, its `contract_class.compressed_program` field is passed straight into `decompress_program` via `user_deprecated_contract_class_to_sn_api`: [3](#0-2) 

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

The `// TODO(dan): add time and size limits.` comment confirms the omission is known but unaddressed. A small gzip payload (a "zip bomb") can expand to gigabytes when decompressed by `read_to_end`, which keeps growing the `Vec<u8>` until memory is exhausted or the allocator aborts the process — this mirrors the GGUF bug class where an untrusted, unchecked length value drives excessive resource consumption/panic. By contrast, the codebase already has the correct pattern elsewhere: `decompress_with_size_limit` caps decompressed bytes via `.take(max_size + 1)` before deciding success/failure. [4](#0-3) 

### Impact Explanation
A single unauthenticated JSON-RPC call to `starknet_simulate_transactions` containing a crafted, highly-compressed `program` field can force the node to allocate unbounded memory, leading to an out-of-memory crash or severe service degradation of the RPC node process. This directly matches the "network unable to confirm new transactions" criterion if the affected process also serves gateway/simulation duties, and is reachable purely from a submitted (simulated) declare-v1 transaction payload without needing prior state changes or privileges.

### Likelihood Explanation
High likelihood: `simulate_transactions` is a standard, unauthenticated public RPC endpoint; the vulnerable code path is reached simply by submitting a `BroadcastedDeclareTransaction::V1` with a maliciously crafted `compressed_program` field, and the TODO comment confirms no mitigation exists. Gzip bombs achieving >1000x expansion ratios are trivial to construct.

### Recommendation
Replace the unbounded `read_to_end` in `decompress_program` with a size-limited read (e.g., wrap the `GzDecoder` in `.take(max_size)` as done in `decompress_with_size_limit`), and return an error once the limit is exceeded, mirroring the existing `decode_and_decompress_with_size_limit` implementation. Also apply a decompression time budget as the TODO suggests.

### Proof of Concept
1. Construct a highly compressible payload, e.g. `vec![0u8; 1_000_000_000]` (1 GB of zeros), gzip-compress it (compresses to a few KB), and base64-encode it.
2. Submit a JSON-RPC `starknet_simulate_transactions` request with a `BroadcastedTransaction::Declare(BroadcastedDeclareTransaction::V1(...))` whose `contract_class.program` field is set to this base64 string.
3. Observe `decompress_program`'s `decoder.read_to_end(&mut decompressed)` attempt to allocate/write ~1 GB into memory per request; repeated or concurrent requests exhaust node memory and crash or stall the RPC service.

Note: I was unable to fully trace whether other layers (e.g., an outer HTTP body size limit) might partially mitigate this before reaching `decompress_program`; the compressed base64 body itself can be kept small (a few KB) so typical body-size limits would not prevent triggering this path.

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1066-1076)
```rust
    #[instrument(skip(self, transactions), level = "debug", err, ret)]
    async fn simulate_transactions(
        &self,
        block_id: BlockId,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
    ) -> RpcResult<Vec<SimulatedTransaction>> {
        trace!("Simulating transactions: {:#?}", transactions);
        let executable_txns =
            transactions.into_iter().map(|tx| tx.try_into()).collect::<Result<_, _>>()?;

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
