### Title
Unbounded gzip decompression of user-supplied Cairo0 `program` in `decompress_program` enables memory-exhaustion DoS via `starknet_estimateFee` — ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The JSON-RPC `decompress_program` helper decompresses a base64+gzip encoded `compressed_program` field taken directly from a user-submitted `BroadcastedDeclareTransaction` (V1) with no cap on the decompressed size, unlike every other decompression path in the codebase (`decompress_with_size_limit`, `decode_and_decompress_with_size_limit`, storage's `MAX_DECOMPRESSED_SIZE`). A small malicious gzip payload can expand to gigabytes in memory, exhausting heap/CPU on the RPC node executing `estimateFee`/simulation requests — the same "unbounded decompression of untrusted metadata" bug class as the ExifReader advisory.

### Finding Description
`decompress_program` reads the entire gzip stream into memory with `read_to_end` and has an explicit `// TODO(dan): add time and size limits.` comment acknowledging the missing bound: [1](#0-0) 

This function is invoked by `user_deprecated_contract_class_to_sn_api`, which builds a `starknet_api::deprecated_contract_class::ContractClass` straight from the caller-supplied `compressed_program` string of a `BroadcastedDeclareV1Transaction`: [2](#0-1) 

That conversion is reached by `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` for the `V1` variant: [3](#0-2) 

This `TryFrom` is invoked by the `estimate_fee` RPC handler, which maps every element of the caller-supplied `Vec<BroadcastedTransaction>` through `tx.try_into()` before any fee/resource bound is checked: [4](#0-3) 

By contrast, every other place in the codebase that decompresses user- or network-supplied compressed data enforces an explicit byte cap before or during decompression, e.g. `decompress_with_size_limit` / `decode_and_decompress_with_size_limit` used by the gateway/declare-transaction and P2P class-sync paths: [5](#0-4) [6](#0-5) 

and the storage layer's `MAX_DECOMPRESSED_SIZE` bound on `zstd` decompression: [7](#0-6) [8](#0-7) 

`decompress_program` is the sole exception where an unprivileged JSON-RPC caller's raw compressed bytes are expanded without any bound.

### Impact Explanation
Any unprivileged client calling `starknet_estimateFee` (or `starknet_simulateTransactions`, which shares the same `BroadcastedTransaction` → `ExecutableTransactionInput` conversion) with a `DECLARE` V1 transaction can supply a gzip bomb as `contract_class.program`. A payload of tens/hundreds of KB can expand to hundreds of MB or more in the node's memory before any validation or size check occurs, since the decompression happens unconditionally inside the request-conversion path prior to fee/resource accounting. Repeated or concurrent requests can exhaust node memory/CPU, degrading or crashing the RPC/execution service that serves `estimateFee`/`simulateTransactions`, i.e., a resource-exhaustion denial of service triggered by a single, unauthenticated transaction submission — matching CWE-409 in the referenced ExifReader advisory.

### Likelihood Explanation
High: no authentication, staking, or special privilege is required — any external caller of the public JSON-RPC `estimateFee` endpoint can trigger it with a single crafted declare transaction. The vulnerable code path executes synchronously as part of standard fee-estimation/simulation request handling.

### Recommendation
Route `decompress_program`'s decompression through the existing bounded utility (`decompress_with_size_limit` / `decode_and_decompress_with_size_limit` from `starknet_api::compression_utils`), enforcing the same `MAX_CAIRO0_PROGRAM_SIZE`-style cap already applied in the P2P class-conversion path (`crates/apollo_protobuf/src/converters/class.rs`), and reject/short-circuit oversized decompressed output before allocating further memory.

### Proof of Concept
1. Craft a gzip-compressed JSON blob (e.g., repeated 'a' characters or all-zero bytes) that compresses to a few hundred KB but decompresses to several hundred MB (standard "gzip bomb", akin to the ExifReader ~1000x expansion PoC).
2. Base64-encode it and place it as `contract_class.program` in a `BROADCASTED_DECLARE_TXN_V1` payload.
3. Send it via `starknet_estimateFee` (or `starknet_simulateTransactions`) to the RPC node.
4. Observe `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) call `read_to_end` with no size limit, allocating the full decompressed buffer in the node's process memory, consuming CPU/heap disproportionate to the request size.

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L997-1019)
```rust
    #[instrument(skip(self, transactions), level = "debug", err, ret)]
    async fn estimate_fee(
        &self,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
        block_id: BlockId,
    ) -> RpcResult<Vec<FeeEstimation>> {
        trace!("Estimating fee of transactions: {:#?}", transactions);
        let validate = !simulation_flags.contains(&SimulationFlag::SkipValidate);

        let storage_txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };

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

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-136)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

```

**File:** crates/apollo_storage/src/compression_utils.rs (L15-17)
```rust
pub(crate) const MAX_DECOMPRESSED_SIZE: usize = 1 << 28; // 256 MB
// The compression level to use. Higher levels are slower but compress better.
const COMPRESSION_LEVEL: i32 = zstd::DEFAULT_COMPRESSION_LEVEL;
```

**File:** crates/apollo_storage/src/compression_utils.rs (L50-52)
```rust
pub fn decompress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    zstd::bulk::decompress(data, MAX_DECOMPRESSED_SIZE)
}
```
