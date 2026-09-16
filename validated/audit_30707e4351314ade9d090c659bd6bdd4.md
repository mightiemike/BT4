### Title
Unbounded gzip decompression in JSON-RPC Declare V1 broadcasted-transaction handling allows resource-exhaustion DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The `starknet_estimateFee` / `starknet_simulateTransactions` RPC entry points accept a `BroadcastedDeclareV1Transaction` from an untrusted caller and convert it into an `ExecutableTransactionInput` via `TryFrom<BroadcastedDeclareTransaction>`. This conversion calls `user_deprecated_contract_class_to_sn_api`, which in turn calls `decompress_program` to gzip-decompress the caller-supplied `compressed_program` field. Unlike every other gzip/zstd decompression path in this codebase, `decompress_program` reads the decompressed stream to completion with **no size limit and no time limit**, exactly matching the "xmppbomb" bug class (CVE-2014-2741): an attacker-controlled compressed input can expand to an enormous decompressed size, exhausting memory/CPU on the node that processes it. [1](#0-0) 

### Finding Description
`decompress_program` is defined as:

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

The explicit `// TODO(dan): add time and size limits.` comment confirms the missing bound was a known gap. This function is invoked from the write path that converts a broadcasted Declare V1 transaction into something the execution engine understands:

```rust
BroadcastedDeclareTransaction::V1(BroadcastedDeclareV1Transaction { contract_class, .. }) => {
    let sn_api_contract_class =
        user_deprecated_contract_class_to_sn_api(contract_class)?;
    ...
}
``` [2](#0-1) 

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
``` [3](#0-2) 

By contrast, every other decompression path for user/network-supplied contract-class data in this codebase enforces an explicit output-size cap before allocating memory, e.g.:
- `decode_and_decompress_with_size_limit`/`decompress_with_size_limit` in `starknet_api`, which uses `.take(max_size + 1)` before `read_to_end` and errors if the limit is exceeded. [4](#0-3) 
- The gateway's `DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` and the Cairo0 p2p-protobuf class conversion, both of which call `decode_and_decompress_with_size_limit` with a bounded `max_size` (e.g. 4MB for Cairo0 programs). [5](#0-4) [6](#0-5) 
- Storage's `zstd::bulk::decompress` uses `MAX_DECOMPRESSED_SIZE` (256MB) as an explicit bound. [7](#0-6) 

`decompress_program` is the one outlier that omits any bound, and it sits directly on a path reachable by any unprivileged JSON-RPC caller submitting a Declare V1 transaction for fee estimation or simulation.

### Impact Explanation
A malicious caller can craft a small gzip-compressed base64 payload (a classic "gzip bomb", e.g. a few KB compressing megabytes-to-gigabytes of repetitive `0` bytes) and submit it as the `contract_class.program` field of a `starknet_estimateFee` or `starknet_simulateTransactions` request containing a `BROADCASTED_DECLARE_TXN_V1`. The node will decompress the entire payload into an unbounded `Vec<u8>` before ever validating the JSON structure, size limits, or signature/fee bounds that apply to real Declare transactions submitted through the gateway. This can exhaust the RPC node's memory or CPU, causing denial of service to that node's JSON-RPC service (crash/OOM or severe latency), i.e. a network unable to confirm/serve new transactions/requests from that node — a resource-consumption DoS matching the CWE-400 class of the referenced advisory.

### Likelihood Explanation
High: the RPC endpoint is public-facing, requires no authentication, no fee payment, and no prior on-chain state (it's an estimate/simulate call, not a state-changing transaction admitted into the mempool). The only work the attacker needs to do is construct a compressed blob; gzip bombs achieving compression ratios in the thousands are trivial to generate.

### Recommendation
Apply the same bounded decompression pattern used elsewhere in the codebase (`decompress_with_size_limit` / `.take(max_size)`) inside `decompress_program`, enforcing both a maximum decompressed size (mirroring `MAX_CAIRO0_PROGRAM_SIZE` used in the protobuf/gateway paths) and ideally a decompression time budget, rejecting the request with an error before allocating unbounded memory.

### Proof of Concept
1. Generate a gzip-compressed program of e.g. 5KB compressed that expands to >1GB (repeating byte pattern compresses extremely well with gzip).
2. Base64-encode the compressed bytes and place them in the `program` field of a `CONTRACT_CLASS` used in a `BROADCASTED_DECLARE_TXN_V1`.
3. Call `starknet_estimateFee` or `starknet_simulateTransactions` on the RPC node with this broadcasted declare transaction.
4. Observe the node calling `decompress_program` → `GzDecoder::read_to_end` with no size cap, allocating gigabytes of memory / consuming excessive CPU per request; repeated concurrent requests can OOM-kill or stall the node's RPC service. [1](#0-0)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L482-509)
```rust
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

**File:** crates/apollo_storage/src/compression_utils.rs (L15-52)
```rust
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
