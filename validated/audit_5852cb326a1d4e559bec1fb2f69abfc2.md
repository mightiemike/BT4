### Title
Unbounded gzip decompression bomb in RPC declare-transaction simulation path allows resource-exhaustion DoS - (File: crates/apollo_rpc/src/v0_8/api/mod.rs)

### Summary
The `decompress_program` function in `apollo_rpc` decompresses a user-supplied, base64-encoded gzip blob with **no size or time limit**, unlike every other class-decompression path in the codebase (gateway and P2P paths) which explicitly bound decompressed output via `decode_and_decompress_with_size_limit`/`decompress_with_size_limit`.

### Finding Description
`decompress_program` reads an attacker-controlled base64 string, decodes it, and streams it through a `GzDecoder` into an unbounded `Vec<u8>`: [1](#0-0) 

This function is reachable from `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput`, specifically the `BroadcastedDeclareV1Transaction` branch, via `user_deprecated_contract_class_to_sn_api`: [2](#0-1) 

`ExecutableTransactionInput` is built directly from a `BroadcastedDeclareTransaction`, which is the type accepted by the RPC's simulate/estimate-fee/trace family of write-adjacent endpoints — i.e., a value supplied directly in an unauthenticated JSON-RPC request body from any client, not from consensus-validated storage.

By contrast, every other place in the codebase that decompresses a similarly-encoded gzip blob explicitly enforces a maximum decompressed size using `decompress_with_size_limit`/`decode_and_decompress_with_size_limit` in `crates/starknet_api/src/compression_utils.rs:32-57`, used from the gateway's `DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` (`crates/apollo_http_server/src/deprecated_gateway_transaction.rs:289-301`) and the P2P `Cairo0Class` conversion which hard-caps at 4MB (`crates/apollo_protobuf/src/converters/class.rs:131-137`). The RPC's `decompress_program` is the sole outlier, marked with an explicit unresolved TODO: `// TODO(dan): add time and size limits.` at line 677.

A gzip stream can achieve compression ratios well over 1000:1 (a "zip bomb"), so a small (e.g. a few-hundred-KB) request body can expand to gigabytes in memory, and the CPU time spent inflating it is similarly unbounded.

### Impact Explanation
An unauthenticated caller can submit a single crafted `BroadcastedDeclareV1Transaction` (via simulate/estimate-fee style RPC calls that route through this conversion) whose `compressed_program` field is a gzip bomb. Processing it allocates unbounded memory and CPU on the sequencer's RPC-serving process until it exhausts host memory or is OOM-killed, denying service to that node/process. This maps to the "network unable to confirm new transactions" impact category if the affected process is load-bearing for RPC-driven simulation/estimation used by wallets/relayers, and is a resource-amplification (compression-bomb) bug class directly analogous to CVE-2024-54016.

### Likelihood Explanation
High: the input is a plain base64 string in a JSON-RPC request, requires no special privileges, no on-chain fee payment (only fee estimation/simulation endpoints trigger this path, prior to any execution/fee charge), and gzip bombs of extreme compression ratios are trivial to construct.

### Recommendation
Replace the direct `GzDecoder`/`read_to_end` call in `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) with the existing size-limited helper `decompress_with_size_limit`/`decode_and_decompress_with_size_limit` from `starknet_api::compression_utils`, using the same bound already applied to the equivalent Cairo0 program elsewhere (e.g., `MAX_CAIRO0_PROGRAM_SIZE` in `crates/apollo_protobuf/src/converters/class.rs:133`), and add a decompression time bound as the existing TODO comment already calls for.

### Proof of Concept
1. Craft a gzip stream that decompresses to several GB from a small (<1MB) compressed payload (a standard zip-bomb construction, e.g. nested/repeated zero blocks).
2. Base64-encode it and place it in the `program` field of a `DeprecatedGatewaySierraContractClass`/`compressed_program` field of a `BroadcastedDeclareV1Transaction`.
3. Submit it to an RPC endpoint that converts a `BroadcastedDeclareTransaction` into `ExecutableTransactionInput` (e.g., simulate/estimate-fee call path reaching `TryFrom<BroadcastedDeclareTransaction>` at `crates/apollo_rpc/src/v0_8/api/mod.rs:478-519`).
4. Observe `decompress_program` (lines 671-682) decompress the payload with no size/time cap, driving unbounded memory/CPU usage on the node.

**Note on verification confidence:** I was not able to fully trace which externally-exposed HTTP/RPC method names dispatch into this exact `TryFrom<BroadcastedDeclareTransaction>` conversion (e.g. `simulateTransactions`, `estimateFee`, `traceTransaction`) within the indexed portion of `apollo_rpc/src/v0_8/api/api_impl.rs`, since that file's contents were only partially returned by search. The core vulnerable code path (`decompress_program` lacking size/time limits, contrasted with the size-limited equivalents elsewhere) is confirmed directly from source.

### Citations

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
