### Title
Unbounded gzip decompression of user-supplied Declare-v1 contract program enables memory-exhaustion DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in the JSON-RPC execution path decompresses a user-supplied base64+gzip `compressed_program` field with no output-size limit, unlike every other decompression path in this codebase (all of which enforce explicit size caps). A single unprivileged `starknet_estimateFee` / `starknet_simulateTransactions` (or `addDeclareTransaction`) call carrying a `BroadcastedDeclareV1Transaction` can trigger unbounded in-memory decompression (a "zip bomb"), directly mirroring the Mattermost CVE-2020-14453 class of bug: reads from an untrusted stream are not size-restricted, enabling a DoS.

### Finding Description
`decompress_program` is defined as: [1](#0-0) 

It calls `GzDecoder::read_to_end` with no `.take(...)` bound and no post-hoc size check — the `// TODO(dan): add time and size limits.` comment confirms the gap is acknowledged but unfixed.

This is reachable from an unprivileged, single-transaction RPC call: `BroadcastedDeclareTransaction::V1` → `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` → `user_deprecated_contract_class_to_sn_api` → `decompress_program`: [2](#0-1) 

This conversion is invoked directly inside `estimate_fee` and `simulate_transactions`, both public RPC methods that execute locally against the node's storage/state (no gateway-level gzip/base64 size gating applies to this path): [3](#0-2) [4](#0-3) 

By contrast, every other decompression routine in the codebase enforces an explicit maximum decompressed size:
- `decode_and_decompress_with_size_limit` in `starknet_api` (`.take(max_size + 1)` then checked): [5](#0-4) 
- Storage's `decompress` bounded by `MAX_DECOMPRESSED_SIZE` (256 MB): [6](#0-5) 
- P2P class conversion enforces a 4 MB cap via `decode_and_decompress_with_size_limit`: [7](#0-6) 

`decompress_program` is the sole outlier that omits this protection on a path directly reachable from an untrusted, unauthenticated RPC caller submitting a single declare transaction for local execution/estimation.

### Impact Explanation
A gzip payload can achieve compression ratios well over 1000:1. A modest-size base64 string (a few hundred KB, well within typical HTTP body limits for a JSON-RPC request) can decompress to gigabytes in `decompressed: Vec<u8>` held entirely in memory before any size check occurs, since the check happens only after `read_to_end` completes. Concurrent or repeated calls to `starknet_estimateFee` / `starknet_simulateTransactions` with such payloads can exhaust node memory, causing the RPC/full node process to OOM or become unresponsive — a denial of service that can prevent the node from serving requests, including read/sync services relied upon for confirming transactions.

### Likelihood Explanation
High likelihood: no authentication, staking, or special privilege is required — any RPC client can call `starknet_estimateFee`/`starknet_simulateTransactions` with a crafted `BROADCASTED_DECLARE_TXN_V1` payload. The only gating is the outer JSON-RPC HTTP body size limit, which bounds the *compressed* input, not the *decompressed* output — exactly the type of amplification gzip bombs are designed to exploit.

### Recommendation
Apply the same bounded-decompression pattern already used elsewhere in the codebase (e.g. `decode_and_decompress_with_size_limit`) to `decompress_program`: wrap the `GzDecoder` in a `.take(max_size + 1)` reader and reject the class if the decompressed size exceeds a configured maximum (e.g., matching `MAX_CAIRO0_PROGRAM_SIZE` used in `apollo_protobuf`'s class converter, or the gateway's `max_contract_bytecode_size`), returning an RPC error instead of continuing to inflate memory.

### Proof of Concept
1. Craft a Cairo0 "program" JSON blob and gzip-compress it at maximum ratio (e.g., a huge run of repeated bytes or a crafted highly-compressible JSON structure), then base64-encode it to form `compressed_program`.
2. Submit a JSON-RPC request to `starknet_estimateFee` (or `starknet_simulateTransactions`) with a `BROADCASTED_DECLARE_TXN_V1` whose `contract_class.program` is this crafted `compressed_program`, along with arbitrary but well-formed `sender_address`, `nonce`, `max_fee`, `signature` fields (values need not be valid on-chain; decompression happens before further validation).
3. Observe that `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) decompresses the payload fully into memory via `GzDecoder::read_to_end` before any size check, causing large/unbounded memory allocation proportional to the crafted compression ratio, independent of the compressed request body size.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L221-245)
```rust
    /// Estimates the fee of a series of transactions.
    #[method(name = "estimateFee")]
    async fn estimate_fee(
        &self,
        request: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
        block_id: BlockId,
    ) -> RpcResult<Vec<FeeEstimation>>;

    /// Estimates the fee of a message from L1.
    #[method(name = "estimateMessageFee")]
    async fn estimate_message_fee(
        &self,
        message: MessageFromL1,
        block_id: BlockId,
    ) -> RpcResult<FeeEstimation>;

    /// Simulates execution of a series of transactions.
    #[method(name = "simulateTransactions")]
    async fn simulate_transactions(
        &self,
        block_id: BlockId,
        transactions: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
    ) -> RpcResult<Vec<SimulatedTransaction>>;
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L478-530)
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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L997-1075)
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

        let block_number = get_accepted_block_number(&storage_txn, block_id)?;
        let block_not_reverted_validator =
            BlockNotRevertedValidator::new(block_number, &storage_txn)?;
        drop(storage_txn);
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let execution_config = self.execution_config;

        let chain_id = self.chain_id.clone();
        let reader = self.storage_reader.clone();
        let class_manager_client =
            create_class_manager_client(self.class_manager_client.clone()).await;

        let estimate_fee_result = tokio::task::spawn_blocking(move || {
            exec_estimate_fee(
                executable_txns,
                &chain_id,
                reader,
                maybe_pending_data,
                state_number,
                block_number,
                &execution_config,
                validate,
                DONT_IGNORE_L1_DA_MODE,
                class_manager_client,
            )
        })
        .await
        .map_err(internal_server_error)?;

        block_not_reverted_validator.validate(&self.storage_reader)?;

        match estimate_fee_result {
            Ok(Ok(fees)) => Ok(fees),
            Ok(Err(reverted_tx)) => {
                Err(ErrorObjectOwned::from(JsonRpcError::<TransactionExecutionError>::from(
                    TransactionExecutionError {
                        transaction_index: reverted_tx.index,
                        execution_error: reverted_tx.revert_reason,
                    },
                )))
            }
            Err(err) => Err(internal_server_error(err)),
        }
    }

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

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-137)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

        Ok(Self { program, entry_points_by_type, abi })
```
