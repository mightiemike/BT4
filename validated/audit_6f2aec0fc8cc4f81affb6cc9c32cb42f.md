### Title
Unbounded Gzip Decompression in `starknet_simulateTransactions` DeclareV1 Contract Class Path Allows Memory-Exhaustion DoS - (File: `crates/apollo_rpc/src/v0_8/api/mod.rs`)

### Summary
The `starknet_simulateTransactions` JSON-RPC method (and any other path that converts a `BroadcastedDeclareTransaction::V1` into an `ExecutableTransactionInput`) decompresses a user-supplied, base64-encoded gzip blob via `decompress_program` with no size or time limit, unlike every other decompression path in the codebase.

### Finding Description
`TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` handles the `V1` variant by calling `user_deprecated_contract_class_to_sn_api(contract_class)`, which in turn calls `decompress_program(&value.compressed_program)`: [1](#0-0) [2](#0-1) 

`decompress_program` itself performs an unbounded `GzDecoder::read_to_end` into memory with an explicit `TODO` acknowledging the missing limit: [3](#0-2) 

This is the exact bug class described in the CrowdSec advisory: a small compressed payload (`compressed_program` field of `BroadcastedDeclareV1Transaction`) can expand to an arbitrarily large decompressed buffer, since `flate2::read::GzDecoder::read_to_end` has no cap. `BroadcastedDeclareV1Transaction.contract_class` is a `DeprecatedContractClass` whose `compressed_program` field is exactly this attacker-controlled base64+gzip string: [4](#0-3) [5](#0-4) 

`BroadcastedDeclareTransaction` is a parameter of the public `starknet_simulateTransactions` RPC method, reachable by any client without authentication: [6](#0-5) [7](#0-6) 

This contrasts sharply with every other decompression path in the repo, which is size-bounded: the HTTP gateway's declare path uses `decode_and_decompress_with_size_limit`, which caps the decompressed size via `.take(max_size + 1)`: [8](#0-7) [9](#0-8) 

and storage-layer decompression uses `zstd::bulk::decompress` bounded by `MAX_DECOMPRESSED_SIZE`: [10](#0-9) 

`decompress_program` is the sole outlier lacking any bound, and it sits directly on the unauthenticated RPC ingestion path for simulated Declare V1 transactions.

### Impact Explanation
A single RPC caller can submit a `starknet_simulateTransactions` request containing a `BroadcastedDeclareV1Transaction` whose `compressed_program` is a small gzip bomb (e.g., kilobytes compressed expanding to hundreds of MB or more). Because `decompress_program` reads to completion with `read_to_end` before any size check, the RPC node process will allocate unbounded memory per request. Sending several such requests concurrently can exhaust node memory, causing the RPC/full-node process to be OOM-killed or become unresponsive — denying JSON-RPC service (including transaction submission, state queries used by wallets/bouncers, etc.) for that node. This matches the CWE-409/DoS impact class of the source advisory, applied here to a full-node RPC service rather than the sequencer's own consensus-critical mempool/gateway path (which is separately protected). While this does not corrupt state or cause fund loss, it can make an RPC endpoint unable to serve requests, which is a legitimate node-availability-DoS finding at Medium severity.

### Likelihood Explanation
No authentication or special privilege is required — the RPC method is a public JSON-RPC read/simulation endpoint intended for general dApp/wallet use. Constructing a gzip bomb wrapped in valid-looking (but not necessarily accepted-onchain) `DeprecatedContractClass` JSON is trivial and can be scripted by any external caller. The `TODO(dan): add time and size limits` comment directly confirms the missing mitigation was known but unimplemented.

### Recommendation
Bound `decompress_program` the same way `decompress_with_size_limit` in `starknet_api::compression_utils` does: wrap the `GzDecoder` in a `.take(max_size + 1)` reader (or equivalent streaming cap tied to `max_sierra_program_size`/an analogous config), and reject with a clear error once the limit is exceeded, instead of calling `read_to_end` unconditionally. Apply this consistently to any other RPC-reachable `GzDecoder`/`ZstdDecoder` usage that lacks equivalent limits.

### Proof of Concept
1. Craft a `DeprecatedContractClass.program` (`compressed_program`) field as a highly compressible gzip payload, e.g. gzip-compress `"0".repeat(500_000_000)` (base64-encoded); the compressed size will be only a few KB.
2. Submit a JSON-RPC request to `starknet_simulateTransactions` with a `BroadcastedDeclareV1Transaction` embedding this payload as `contract_class.program`.
3. On the server, `TryFrom<BroadcastedDeclareTransaction>::try_from` → `user_deprecated_contract_class_to_sn_api` → `decompress_program` will allocate the entire ~500MB decompressed buffer in memory with no limit check.
4. Repeating this concurrently against the target node exhausts memory and can crash/OOM-kill the RPC process, denying service.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L238-245)
```rust
    /// Simulates execution of a series of transactions.
    #[method(name = "simulateTransactions")]
    async fn simulate_transactions(
        &self,
        block_id: BlockId,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
    ) -> RpcResult<Vec<SimulatedTransaction>>;
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L478-520)
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

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L267-276)
```rust
#[derive(Debug, Clone, Default, Eq, PartialEq, Deserialize, Serialize)]
pub struct DeprecatedContractClass {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(default)]
    pub abi: Option<Vec<DeprecatedContractClassAbiEntry>>,
    #[serde(rename = "program")]
    // TODO(shahak): Create a struct for a compressed base64 value.
    pub compressed_program: String,
    pub entry_points_by_type: HashMap<EntryPointType, Vec<DeprecatedEntryPoint>>,
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
