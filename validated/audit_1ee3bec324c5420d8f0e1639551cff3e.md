### Title
Unbounded gzip decompression of user-supplied program in deprecated declare transaction path enables decompression-bomb DoS - (File: crates/apollo_rpc/src/v0_8/api/mod.rs)

### Summary
`decompress_program` in the RPC crate decompresses an attacker-controlled, base64-encoded gzip blob with no size or time limit before parsing it as JSON, unlike the sibling implementation `decode_and_decompress_with_size_limit` in `starknet_api`, which explicitly bounds decompressed output. This mirrors the urllib3 CVE-2025-66471 bug class: fully materializing decompressed attacker data with no cap, enabling a compression-bomb resource-exhaustion attack.

### Finding Description
`decompress_program` decodes base64 input and decompresses it via `GzDecoder::read_to_end`, with an explicit `// TODO(dan): add time and size limits.` marking the missing safeguard: [1](#0-0) 

This is invoked from `user_deprecated_contract_class_to_sn_api`, which converts a client/user-submitted deprecated (Cairo0) contract class — containing a `compressed_program` field — into the internal `starknet_api` `Program` representation: [2](#0-1) 

By contrast, the equivalent decompression helper used on the gateway's declare-transaction ingestion path (`starknet_api::compression_utils`) enforces a hard cap via `.take(max_size + 1)` and returns `SizeLimitExceeded` if violated: [3](#0-2) 

and this bounded helper is what the primary gateway declare path (`DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class`) uses: [4](#0-3) 

Storage-layer decompression of `SierraContractClass`/`DeprecatedContractClass`/`CasmContractClass` also enforces `MAX_DECOMPRESSED_SIZE` (256 MB) via `zstd::bulk::decompress`: [5](#0-4) 

The `decompress_program` function in `apollo_rpc` is the outlier: it has no equivalent bound, so a small, highly-compressed gzip payload embedded in a submitted deprecated-declare transaction's `compressed_program` field can expand to an arbitrarily large in-memory buffer, exactly matching the urllib3 advisory's bug class (CWE-409: unbounded decompression of attacker-controlled data).

### Impact Explanation
An unprivileged transaction sender submitting a deprecated (Cairo0) declare transaction with a crafted `compressed_program` (e.g., a gzip bomb decompressing from kilobytes to gigabytes) can force the RPC node processing the request to allocate excessive memory and burn CPU while decompressing/parsing, causing denial of service to that node. If this code path is invoked on nodes servicing transaction submission/validation, sustained requests of this kind can degrade or halt the node's ability to process further transactions, which is a High-severity availability impact consistent with the original CVE.

### Likelihood Explanation
Likelihood is high for reachability: the vulnerable function is fed directly from user-supplied `compressed_program` content in a declare transaction/class-conversion path, requires no special privileges, and the missing bound is explicitly flagged in-code as a known gap (`TODO(dan): add time and size limits.`), indicating the safeguard was never implemented for this call site while an equivalent safeguard was added elsewhere in the same codebase.

### Recommendation
Apply the same bounded-decompression pattern already used elsewhere in the codebase (`decompress_with_size_limit` / `MAX_DECOMPRESSED_SIZE`) to `decompress_program`: wrap the `GzDecoder` reader with a `.take(max_size + 1)` limit, reject inputs whose decompressed size exceeds the configured maximum, and consider adding a decompression time budget as the existing TODO suggests.

### Proof of Concept
1. Craft a gzip payload where a few KB of base64-encoded compressed bytes decompress to several hundred MB/GB of repetitive JSON-like data (a standard "zip bomb" construction).
2. Submit it as the `compressed_program` field of a deprecated (Cairo0) declare-transaction contract class through the code path that calls `user_deprecated_contract_class_to_sn_api` → `decompress_program`.
3. Observe that `decoder.read_to_end(&mut decompressed, ...)` proceeds without any size cap, allocating memory proportional to the decompressed size and consuming CPU, before any subsequent validation (e.g., program size checks) is applied — unlike the bounded gateway path in `deprecated_gateway_transaction.rs`.

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
