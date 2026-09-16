### Title
Unbounded gzip decompression and JSON parsing of attacker-supplied `Cairo0` program in RPC declare-transaction execution path allows memory/CPU exhaustion - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The Starknet RPC node's `decompress_program` helper Base64-decodes an untrusted `compressed_program` string, gzip-decompresses it, and parses the result as JSON — with no size limit at any of these three steps. This function is invoked whenever a client submits a `BroadcastedDeclareTransaction::V1` to an execution-simulation JSON-RPC method (e.g. `starknet_estimateFee` / `starknet_simulateTransactions`), i.e. for a transaction that has not been (and need not be) accepted, funded, or signature-checked. This mirrors exactly the RUSTSEC-2026-0229 bug class: base64 decode + decompress + JSON-parse of an unauthenticated value before any application-level size limit is applied.

### Finding Description
`decompress_program` performs: [1](#0-0) 
1. `base64::decode` (called twice — the first result is discarded, itself a wasted allocation) on the caller-supplied `String`.
2. Constructs a `GzDecoder` over the decoded bytes and calls `read_to_end`, which decompresses the *entire* gzip stream into a `Vec<u8>` with **no cap**. A small Base64 payload can inflate to a very large decompressed buffer (classic "zip bomb" amplification), exactly the scenario the fixed NIP-98 parser addresses by capping decoded output before parsing.
3. `serde_json::from_reader` then parses the arbitrarily large decompressed buffer.

The TODO left in the code explicitly acknowledges the gap: `// TODO(dan): add time and size limits.`

This is reachable via `user_deprecated_contract_class_to_sn_api`, which is called from the `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput` conversion for the V1 variant: [2](#0-1) 

This conversion path is used by execution-simulation RPC methods (fee estimation / simulate-transaction endpoints) that accept a raw, unsigned, un-vetted `BroadcastedDeclareTransaction` directly from the caller — there is no prior stateless validator step analogous to `apollo_gateway`'s `StatelessTransactionValidator::validate_class_length`, which does enforce `max_contract_bytecode_size` / `max_contract_class_object_size` before allowing a declare transaction into the gateway/mempool pipeline: [3](#0-2) 

By contrast, every other Base64/gzip decode path in the codebase that touches untrusted input properly bounds the decompressed size before JSON parsing, using `decode_and_decompress_with_size_limit`: [4](#0-3) 
This is used consistently for the deprecated gateway declare flow and for p2p Cairo0 class sync (bounded to 4 MiB): [5](#0-4) [6](#0-5) 

`decompress_program` in `apollo_rpc` is the one outlier that skips this size-limiting wrapper entirely.

### Impact Explanation
An RPC node exposes `estimate_fee`/`simulate_transactions` (or similar) endpoints publicly to arbitrary callers, since these do not require an accepted, funded, or previously-declared transaction — the caller supplies the raw `BroadcastedDeclareTransaction::V1` payload including the Base64-compressed Cairo0 `program`. A malicious caller can send a crafted small Base64 payload that decompresses to a very large buffer, forcing the node to allocate large amounts of memory and burn CPU cycles in `GzDecoder::read_to_end` and `serde_json::from_reader`, repeated across concurrent requests. This can degrade or crash the RPC node process, impacting availability of the RPC/read/simulate path for legitimate users of that node. Unlike the exploited HTTP-header case in the report, actual bound here is only the overall RPC request body size limit (if configured) — the compressed payload itself can stay small while still expanding hugely after decompression, so a modest request-body cap does not prevent the amplification.

### Likelihood Explanation
High: the vulnerable function is directly reachable from a single, unauthenticated (or minimally authenticated, per node RPC exposure policy) JSON-RPC call with attacker-fully-controlled content, requires no prior state changes, no fees, and no valid signature. The code path is a "simulate a transaction that could be sent," which by design is meant to accept arbitrary, unvalidated transaction fields for fee estimation purposes — making it trivially reachable by any RPC client.

### Recommendation
Replace the manual Base64-decode/GzDecoder/serde_json sequence in `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) with the existing bounded helper `starknet_api::compression_utils::decode_and_decompress_with_size_limit`, using a size limit consistent with other Cairo0 program limits already used elsewhere in the codebase (e.g. the `MAX_CAIRO0_PROGRAM_SIZE = 4 MiB` constant used in `apollo_protobuf/src/converters/class.rs`). Remove the redundant duplicate `base64::decode` call, and ensure the size limit is enforced before decompression completes (as the shared helper does via `.take(max_size + 1)`).

### Proof of Concept
1. Craft a small Base64 string that decodes to a gzip stream which decompresses into a very large (e.g. multi-GB) buffer of repeated bytes (a standard "zip bomb" construction — gzip supports extremely high compression ratios for repetitive data).
2. Send a JSON-RPC request to an exposed node's `starknet_estimateFee` (or `starknet_simulateTransactions`) method with a `BroadcastedDeclareTransaction` of version 1 whose `program` field is this crafted Base64 string.
3. Observe that `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) fully decompresses the payload into memory with no size check before failing (if at all) only at the `serde_json::from_reader` stage — by which point the large allocation and decompression CPU cost has already been incurred.
4. Repeating the request concurrently amplifies memory/CPU consumption on the node, matching the resource-exhaustion pattern of RUSTSEC-2026-0229.

(Note: I could not fully verify from the index alone which exact JSON-RPC method names route through `TryFrom<BroadcastedDeclareTransaction> for ExecutableTransactionInput`, nor whether an additional global request-body-size limit is configured on the `apollo_rpc` server that would bound the compressed input size — `crates/apollo_rpc/src/lib.rs` references body-size limiting but I was unable to confirm the configured default within available context. This does not affect the amplification argument, since a small compressed payload can still expand to a very large decompressed one.)

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L315-337)
```rust
    fn validate_class_length(
        &self,
        contract_class: &starknet_api::state::SierraContractClass,
    ) -> StatelessTransactionValidatorResult<()> {
        if contract_class.sierra_program.len() > self.config.max_contract_bytecode_size {
            return Err(StatelessTransactionValidatorError::ContractBytecodeSizeTooLarge {
                contract_bytecode_size: contract_class.sierra_program.len(),
                max_contract_bytecode_size: self.config.max_contract_bytecode_size,
            });
        }

        let contract_class_object_size = serde_json::to_string(&contract_class)
            .expect("Unexpected error serializing contract class.")
            .len();
        if contract_class_object_size > self.config.max_contract_class_object_size {
            return Err(StatelessTransactionValidatorError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self.config.max_contract_class_object_size,
            });
        }

        Ok(())
    }
```

**File:** crates/starknet_api/src/compression_utils.rs (L32-57)
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

/// Decodes the provided data with size limits.
// TODO(dan): consider limiting the time it takes to decompress.
pub fn decode_and_decompress_with_size_limit<T: DeserializeOwned>(
    value: &str,
    max_size: usize,
) -> Result<T, CompressionError> {
    let decoded_data = base64::decode(value)?;
    let decompressed_data = decompress_with_size_limit(decoded_data, max_size)?;
    Ok(serde_json::from_reader(decompressed_data.as_slice())?)
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
