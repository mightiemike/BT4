### Title
Unbounded gzip decompression of user-supplied Declare V1 `compressed_program` causes memory-exhaustion DoS via `decompress_program` - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The JSON-RPC read/execution API's `decompress_program` helper decompresses an attacker-supplied, base64-encoded gzip blob (`BroadcastedDeclareV1Transaction.contract_class.program`) with **no size or time limit**, unlike every other decompression path in the codebase which enforces an explicit `max_size`. A single crafted `starknet_simulateTransactions` / `starknet_estimateFee` / `starknet_traceTransaction`-style request containing a small, highly-compressed "gzip bomb" causes the node to allocate an unbounded amount of memory while decompressing, resulting in a Denial of Service, closely mirroring the reported `uploadPostHandler`-style unbounded-resource-consumption bug class (CWE-400).

### Finding Description
`decompress_program` reads a base64 string, decodes it, and decompresses it into memory with `decoder.read_to_end(&mut decompressed)` — with a `// TODO(dan): add time and size limits.` comment directly above it, confirming the missing check: [1](#0-0) 

This is invoked when converting a user-submitted `BroadcastedDeclareV1Transaction` into an `ExecutableTransactionInput` for execution: [2](#0-1) 

which is reachable from `TryFrom<BroadcastedDeclareTransaction>`: [3](#0-2) 

and ultimately from `simulate_transactions` (and analogous `estimate_fee`/trace endpoints) which accept a `Vec<BroadcastedTransaction>` directly from the RPC caller and execute them via the blockifier execution pipeline: [4](#0-3) 

This stands in sharp contrast to every other decompression path in the repository, all of which bound the decompressed size explicitly:
- The gateway's declare-tx path uses `decode_and_decompress_with_size_limit`, which caps output via `.take(max_size + 1)`: [5](#0-4) 
- Storage-level decompression caps output at `MAX_DECOMPRESSED_SIZE` (256 MB) via `zstd::bulk::decompress`: [6](#0-5) 
- The HTTP server has an explicit regression test proving it rejects oversized decompressed bodies for the *submission* gateway path: [7](#0-6) 

`decompress_program` is the one remaining decompression sink in the transaction-processing surface that has no such bound, and it sits squarely on the untrusted-input → blockifier-execution path (Declare V1 execution/simulation), which the analog scope explicitly includes.

### Impact Explanation
A gzip "bomb" (a few hundred bytes/KB compressed) can expand to gigabytes when decompressed with `flate2::read::GzDecoder::read_to_end`. Since there is no size cap, the RPC/execution worker thread will attempt to allocate and fill memory proportional to the attacker-chosen decompressed size, exhausting node memory and causing the process (or the OS via OOM-killer) to crash or become unresponsive. Because `estimate_fee`/`simulate_transactions` are commonly exposed, public-facing JSON-RPC methods, this allows any unauthenticated caller to reliably crash or hang a full node / sequencer RPC component — "a network unable to confirm new transactions" if this affects the sequencer's own RPC-serving component, and at minimum a reliable single-request DoS of the RPC service.

### Likelihood Explanation
High. No authentication, staking, or special privilege is required — this is a standard, publicly documented JSON-RPC method (`starknet_estimateFee` / `starknet_simulateTransactions`) accepting attacker-controlled `BROADCASTED_DECLARE_TXN` (V1) input. Building a gzip bomb is trivial and well understood, and the vulnerable code path has an explicit `TODO` acknowledging the missing limit, indicating this is a known but unaddressed gap.

### Recommendation
Replace the unbounded `decoder.read_to_end` in `decompress_program` (crates/apollo_rpc/src/v0_8/api/mod.rs) with a size-limited decompression, mirroring `decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs` (e.g. wrap the decoder with `.take(max_size + 1)` and reject if the result exceeds the configured maximum program size), and add a regression test analogous to `zstd_decompressed_request_too_large` in `crates/apollo_http_server/src/http_server_test.rs` for the RPC decompression path.

### Proof of Concept
1. Craft a `BroadcastedDeclareV1Transaction` whose `contract_class.program` is a base64-encoded gzip stream that decompresses to several GB from a payload of only a few KB (standard "zip bomb" construction, e.g. repeating zero bytes highly compress).
2. Submit it via `starknet_estimateFee` or `starknet_simulateTransactions` to the target node's JSON-RPC endpoint.
3. `decompress_program` (crates/apollo_rpc/src/v0_8/api/mod.rs:671-682) decompresses the payload fully into memory with no size limit, causing large memory allocation and potential OOM/DoS of the RPC-serving process.

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1066-1075)
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

**File:** crates/apollo_storage/src/compression_utils.rs (L12-52)
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
}
```

**File:** crates/apollo_http_server/src/http_server_test.rs (L456-477)
```rust
#[tokio::test]
async fn zstd_decompressed_request_too_large() {
    // 10 KB of repeated bytes — compresses to ~50 bytes with zstd.
    let large_body = vec![b'a'; 10 * 1024];
    let mut encoder = zstd::stream::write::Encoder::new(Vec::new(), 0).unwrap();
    encoder.write_all(&large_body).unwrap();
    let compressed_body = encoder.finish().unwrap();

    // Limit between compressed size and decompressed size.
    // compressed_body is ~50 bytes; decompressed is 10240 bytes.
    let max_request_body_size = large_body.len() - 1;
    assert!(compressed_body.len() < max_request_body_size);

    let http_client = HttpClientServerSetupBuilder::new(unique_u16!())
        .with_max_request_body_size(max_request_body_size)
        .build()
        .await;

    let response = http_client.add_rpc_tx_with_zstd(compressed_body).await;

    assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
}
```
