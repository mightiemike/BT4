Confirmed reachable path: the REST `/gateway/add_transaction` endpoint calls `add_tx` in `crates/apollo_http_server/src/http_server.rs`, which parses the body into `DeprecatedGatewayTransactionV3` and calls `tx.convert_to_rpc_tx(max_sierra_program_size)` **before** any of the `StatelessTransactionValidator::validate` checks (sierra version, contract length, etc.) run. For declare transactions this eventually calls `DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` [1](#0-0) , which calls `decode_and_decompress_with_size_limit` in `starknet_api/src/compression_utils.rs`.

### Title
Uncontrolled decompression time for malformed gzip-compressed `sierra_program` in declare transactions - (File: crates/starknet_api/src/compression_utils.rs)

### Summary
An unprivileged transaction sender submitting a `DECLARE` transaction through the deprecated REST gateway endpoint (`/gateway/add_transaction`) can supply a crafted, malformed gzip stream as the base64-encoded `sierra_program`. This is decompressed by `decompress_with_size_limit` using `flate2::read::GzDecoder`, which enforces only an output **size** cap, not a time/CPU bound, before any of the gateway's other stateless size/version validations run.

### Finding Description
`decode_and_decompress_with_size_limit` decodes the base64 payload and feeds it to a `GzDecoder`, bounding the amount of decompressed bytes with `.take(max_size + 1)` but never bounding the wall-clock/CPU time spent producing those bytes: [2](#0-1) 
The comment on this exact function (`// TODO(dan): consider limiting the time it takes to decompress.`) explicitly acknowledges the missing time bound [3](#0-2) .

This is invoked directly from the deprecated gateway's declare-transaction conversion path, which is reached before the `StatelessTransactionValidator` runs its sierra-version/size checks: [1](#0-0) 
and the HTTP handler invokes this conversion unconditionally for any submitted transaction body: [4](#0-3) 

This is the analogous bug class to CVE-2026-27026: a DEFLATE/gzip stream can be crafted so that decompression (whether via slow byte-by-byte fallback paths, deeply nested back-references, or pathological Huffman tables) consumes disproportionate CPU time relative to the small compressed input, independent of the final output size limit. Since the size limit here only caps *output bytes*, not processing time per byte, a malformed stream that produces output slowly (or errors out only after extensive internal processing) is not mitigated.

There is a second, less-reachable instance of the same pattern in `apollo_rpc`'s `decompress_program`, which has no size or time bound at all (`// TODO(dan): add time and size limits.`), but that function is used for converting externally-fetched feeder-gateway data rather than a live unprivileged transaction submission path, so it is not the primary finding here: [5](#0-4) 

### Impact Explanation
A malicious declare-transaction sender can tie up a gateway worker thread/task for a disproportionate amount of time per request by submitting a small, cheaply-transmitted, specially crafted compressed `sierra_program`. Because this happens on the hot "stateless validation" path invoked for every submitted transaction (before compilation, semaphore-limited compilation, or stateful validation), repeated submissions can degrade gateway throughput and transaction admission latency network-wide — a form of the "network unable to confirm new transactions" impact class, since the gateway is the single entry point that must process every declare transaction before it can reach the mempool.

### Likelihood Explanation
Likelihood is high: the endpoint is unauthenticated (any user can call `/gateway/add_transaction`), the malformed stream is easy to construct client-side (crafting adversarial DEFLATE streams is a well-known technique, as demonstrated by the pypdf disclosure), and no time-boxing, streaming timeout, or task cancellation is present around the decompression call in this code path.

### Recommendation
Wrap `decompress_with_size_limit` (and `decompress_program` in `apollo_rpc`) in a wall-clock timeout (e.g., `tokio::time::timeout` around a spawned blocking task, or manual chunked reads with a deadline check), rather than only bounding decompressed size via `.take()`. Consider moving decompression after the cheaper stateless checks (e.g., after `validate_sierra_version`/size checks operate on already-decompressed data is unavoidable, but at minimum reorder to fail fast on other structural issues first) and consider using a decompression library/backend with bounded per-call CPU cost guarantees, mirroring the pypdf fix referenced in the advisory (PR py-pdf/pypdf#3644).

### Proof of Concept
1. Craft a malformed gzip stream (e.g., with corrupted/adversarial Huffman/back-reference tables designed to maximize CPU cycles per output byte during decompression, similar to techniques used in the pypdf PoC).
2. Base64-encode it and place it as the `sierra_program` field of a `DeprecatedGatewaySierraContractClass` JSON payload for a `DECLARE` V3 transaction.
3. POST the JSON body to `/gateway/add_transaction`.
4. Observe that `add_tx` → `DeprecatedGatewayTransactionV3::convert_to_rpc_tx` → `convert_to_sierra_contract_class` → `decode_and_decompress_with_size_limit` [6](#0-5)  spends an outsized amount of CPU time relative to the compressed payload size before any output-size or format validation can reject the request, since these checks occur only after decompression completes.

### Citations

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

**File:** crates/starknet_api/src/compression_utils.rs (L48-49)
```rust
/// Decodes the provided data with size limits.
// TODO(dan): consider limiting the time it takes to decompress.
```

**File:** crates/apollo_http_server/src/http_server.rs (L198-216)
```rust
    let tx: DeprecatedGatewayTransactionV3 = match serde_json::from_str(&tx) {
        Ok(value) => value,
        Err(e) => {
            validate_supported_tx_version_str(&tx).inspect_err(|e| {
                debug!("Error while validating transaction version: {}", e);
                increment_failure_metrics(e);
            })?;

            debug!("Error while parsing transaction: {}", e);
            check_supported_resource_bounds_and_increment_metrics(&tx);
            return Err(e.into());
        }
    };

    let rpc_tx = tx.convert_to_rpc_tx(max_sierra_program_size).inspect_err(|e| {
        debug!("Error while converting deprecated gateway transaction into RPC transaction: {}", e);
    })?;

    add_tx_inner(app_state, headers, rpc_tx).await
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
