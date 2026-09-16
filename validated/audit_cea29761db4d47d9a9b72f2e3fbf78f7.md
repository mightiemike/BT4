### Title
Unbounded gzip decompression of user-supplied `compressed_program` enables a decompression-bomb DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The `decompress_program` helper in `apollo_rpc` decompresses a base64-encoded gzip blob supplied directly by an RPC caller as part of a legacy (Cairo0) declare transaction's `compressed_program`, but reads the decompressed output with `read_to_end` and no size or time limit, unlike the analogous helper `decode_and_decompress_with_size_limit` used elsewhere in the codebase. This is the same bug class as the python-jose "JWT bomb" (CVE-2024-33664): a small, attacker-controlled compressed payload with an extreme compression ratio causes unbounded memory/CPU consumption during decompression.

### Finding Description
`decompress_program` decodes base64 and decompresses with `GzDecoder`, reading the entire output into memory via `decoder.read_to_end(&mut decompressed, ...)` with an explicit `// TODO(dan): add time and size limits.` comment acknowledging the missing bound. [1](#0-0) 

This function is reached through `user_deprecated_contract_class_to_sn_api`, which is invoked when converting a `BroadcastedDeclareTransaction::V1` (a `BroadcastedDeclareV1Transaction` carrying a `contract_class.compressed_program` field controlled entirely by the RPC caller) into the executable transaction form used by `estimateFee`, `simulateTransactions`, and `addDeclareTransaction`. [2](#0-1) 

These three RPC methods are exposed to any unprivileged sender submitting a declare-v1 transaction or a fee-estimation/simulation request. [3](#0-2) 

By contrast, the codebase already has a size-bounded analog, `decode_and_decompress_with_size_limit`, used for Sierra program decompression in the gateway (`crates/starknet_api/src/compression_utils.rs`), which caps decompressed output using `.take(max_size + 1)`. `decompress_program` in `apollo_rpc` does not use this safe pattern for the legacy Cairo0 program path, leaving an unbounded decompression sink. [4](#0-3) 

### Impact Explanation
A single crafted small gzip payload (a classic "zip bomb", tens of KB compressed expanding to gigabytes) submitted as `compressed_program` in a declare-v1 transaction (or via `estimateFee`/`simulateTransactions` bodies) forces the sequencer's RPC node to allocate unbounded memory and CPU while decompressing into an ever-growing `Vec<u8>`, before any subsequent size validation of the resulting `Program` occurs. This can exhaust node memory or CPU, causing the RPC-facing sequencer process to crash or become unresponsive to legitimate transaction submissions — a network-availability impact (denial of service), matching the CWE-400 resource-consumption class of the referenced advisory.

### Likelihood Explanation
The attack requires only a well-formed JSON-RPC request with a `DECLARE` v1 transaction (or fee-estimation/simulation call) containing a highly compressible gzip payload as `compressed_program` — no special privileges, no valid signature verification bypass, and no prior state are needed since this path is entered before/around class-hash and signature checks during request deserialization/conversion. This makes it trivially and repeatedly triggerable by any external caller with RPC access.

### Recommendation
Replace the unbounded `read_to_end` in `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) with a size-limited read (e.g., `decoder.take(MAX_SIZE).read_to_end(...)` and reject/error if the limit is exceeded), mirroring `decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs`. Additionally consider bounding decompression time/CPU, and apply the same limit consistently to all base64+gzip decompression entry points that consume unauthenticated user input.

### Proof of Concept
1. Craft a gzip file that compresses e.g. 2GB of repeated bytes down to a few KB (`python3 -c "import gzip; open('bomb.gz','wb').write(gzip.compress(b'A'*(2**31), 9))"`).
2. Base64-encode the compressed bytes and place them as `contract_class.program` (i.e., `compressed_program`) in a `BROADCASTED_DECLARE_TXN_V1` JSON body.
3. Submit this body to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_addDeclareTransaction`.
4. Observe `decompress_program` allocating memory proportional to the decompressed size (up to gigabytes) with no early size check, causing high memory/CPU usage or OOM on the node handling the request.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L200-245)
```rust
    /// Submits a new invoke transaction to be added to the chain.
    #[method(name = "addInvokeTransaction")]
    async fn add_invoke_transaction(
        &self,
        invoke_transaction: TypedInvokeTransaction,
    ) -> RpcResult<AddInvokeOkResult>;

    /// Submits a new deploy account transaction to be added to the chain.
    #[method(name = "addDeployAccountTransaction")]
    async fn add_deploy_account_transaction(
        &self,
        deploy_account_transaction: TypedDeployAccountTransaction,
    ) -> RpcResult<AddDeployAccountOkResult>;

    /// Submits a new declare transaction to be added to the chain.
    #[method(name = "addDeclareTransaction")]
    async fn add_declare_transaction(
        &self,
        declare_transaction: BroadcastedDeclareTransaction,
    ) -> RpcResult<AddDeclareOkResult>;

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

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L480-530)
```rust
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
