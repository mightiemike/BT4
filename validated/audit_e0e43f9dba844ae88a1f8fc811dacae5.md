### Title
Unbounded gzip decompression of user-submitted deprecated declare transaction program (decompression-bomb DoS) - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program`, invoked from `user_deprecated_contract_class_to_sn_api` when handling a `BroadcastedDeclareTransaction::V1` sent via the RPC `estimate_fee`/`simulate`/execution paths, decompresses a base64-encoded, gzip-compressed `compressed_program` field taken directly from the RPC caller without any size or time limit, unlike every other decompression path in this codebase.

### Finding Description
`decompress_program` decodes and gzip-decompresses the attacker-supplied `compressed_program` field with `decoder.read_to_end(&mut decompressed)`, which has no cap on the resulting buffer size: [1](#0-0) 

This is called from `user_deprecated_contract_class_to_sn_api`, which is reached via `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` for the `V1` variant — i.e. directly from an externally submitted, unprivileged RPC declare transaction (used for fee estimation / simulation entry points): [2](#0-1) 

Contrast this with every other place in the codebase that performs the analogous "attacker-supplied length/compressed-size field controls decompressed output" operation: `decode_and_decompress_with_size_limit` in `starknet_api::compression_utils`, which explicitly bounds the read with `.take(max_size + 1)` and errors with `CompressionError::SizeLimitExceeded` if exceeded: [3](#0-2) 

That size-limited helper is used for every other user-facing compressed contract-class field (Sierra program, ABI, protobuf Cairo0 program), each bounded by a size constant (e.g. `MAX_CAIRO0_PROGRAM_SIZE`): [4](#0-3) [5](#0-4) 

`decompress_program` is the outlier: its own `// TODO(dan): add time and size limits.` comment confirms the missing bound was known but never fixed: [6](#0-5) 

This is the exact bug-class analog to ALPINE-CVE-2018-14351 (mishandled untrusted length/size field controlling a downstream buffer): here, an attacker-controlled gzip stream's declared/implicit size is trusted with no cap before it is fully materialized into memory (potentially gigabytes from a tiny gzip payload — a classic zip-bomb amplification, e.g. >1000x), and subsequently parsed with `serde_json::from_reader`.

### Impact Explanation
An unprivileged RPC caller can submit a single `BroadcastedDeclareTransaction::V1` with a small, highly-compressible `compressed_program` payload (a gzip bomb) to the fee-estimation/simulation RPC endpoints. The sequencer/full node will attempt to decompress it into an unbounded in-memory buffer, causing large memory allocation and CPU consumption. Because this executes on the RPC-serving process (which in the sequencer stack may be co-located with block-building/execution components), sustained abuse can exhaust node memory/CPU, resulting in denial of service — impairing the node's ability to serve RPC requests and potentially process transactions, i.e. "a network unable to confirm new transactions" if it degrades sequencer/full-node availability broadly.

### Likelihood Explanation
High likelihood of triggering: the path requires only a single crafted RPC request (`estimate_fee`/`simulate_transactions`/`call` with `skip_execute`/declare V1 execution input) with a small gzip bomb payload in the `contract_class.program` field — no special privileges, no prior state, and no interaction with mempool/consensus needed. The vulnerable code path is trivially reachable and the missing check is explicit (unlike other, protected, call sites in the same codebase).

### Recommendation
Replace the unbounded `decoder.read_to_end(&mut decompressed)` in `decompress_program` with the same size-limited decompression helper already used elsewhere (`decode_and_decompress_with_size_limit`), enforcing a maximum decompressed program size (e.g. `MAX_CAIRO0_PROGRAM_SIZE` or an equivalent gateway-configured bound) and returning an RPC error instead of allocating unbounded memory. Consider also bounding decompression time/CPU.

### Proof of Concept
1. Construct a legitimate-looking `BroadcastedDeclareV1Transaction` JSON-RPC payload where `contract_class.program` is a base64-encoded gzip stream that decompresses to several GB of repeated data (a standard "zip bomb", e.g. all-zero data compresses at extremely high ratios with gzip).
2. Submit this transaction to an RPC method that triggers `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` (e.g. `starknet_estimateFee` / `starknet_simulateTransactions` with a V1 declare in the transaction list).
3. Observe `user_deprecated_contract_class_to_sn_api` → `decompress_program` decompress the payload without any size cap, driving process memory usage up to the fully decompressed size and/or causing an OOM/crash or severe latency spike on the node handling the request.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L478-529)
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

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-137)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

        Ok(Self { program, entry_points_by_type, abi })
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L289-302)
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
}
```
