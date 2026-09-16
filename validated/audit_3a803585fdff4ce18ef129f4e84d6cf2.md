Based on the analog to CVE-2024-45700 (uncontrolled decompression resource exhaustion), I found a valid reachable analog in the RPC layer's declare-transaction handling path.

### Title
Unbounded gzip decompression in RPC BroadcastedDeclareTransaction V1 handling causes memory/CPU exhaustion DoS - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program`, used when converting a user-submitted `BroadcastedDeclareTransaction::V1` into an internal `starknet_api` transaction for local execution/simulation RPC methods (e.g. `estimateFee`/`simulateTransactions`/`call` style endpoints), decompresses a user-controlled base64+gzip payload with no size limit and no time limit, unlike the equivalent gateway path.

### Finding Description
The function `decompress_program` reads an arbitrary user-supplied base64 string, decodes it, and pipes it into a `flate2::read::GzDecoder`, calling `read_to_end` without any bound on the output size: [1](#0-0) 

This is invoked from `user_deprecated_contract_class_to_sn_api`, which is reached when converting a `BroadcastedDeclareTransaction::V1` (a Cairo-0/deprecated declare transaction submitted by an unprivileged caller via RPC) prior to local execution: [2](#0-1) 

This contrasts with the properly hardened path used elsewhere in the codebase, `decode_and_decompress_with_size_limit`/`decompress_with_size_limit` in `starknet_api::compression_utils`, which caps decompressed output at a caller-specified `max_size` using `.take(max_size + 1)`: [3](#0-2) 

The gateway's own declare-transaction validation path (`DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` and the stateless validator) does enforce size limits before decompression, but the RPC `decompress_program` function used for `V1` deprecated declare classes does not use this size-limited utility — it is explicitly marked with `// TODO(dan): add time and size limits.`, confirming the maintainers were aware of the gap: [4](#0-3) 

An attacker can craft a small, highly compressible gzip payload (a "zip bomb") that decompresses to gigabytes of data, then submit it as the `program` field of a deprecated (Cairo-0) declare transaction to an RPC method that triggers local class conversion/execution (e.g., fee estimation or simulation endpoints that accept `BroadcastedDeclareTransaction`). This causes the node to allocate excessive memory and burn CPU cycles on decompression, matching the CVE-2024-45700 bug class (uncontrolled resource exhaustion from decompression of attacker-supplied data).

### Impact Explanation
A single unprivileged RPC caller can force a sequencer/full node to allocate unbounded memory and CPU decompressing a small malicious payload, leading to a crash or severe service degradation of the RPC node — a network-availability impact (DoS), which is explicitly an accepted impact category ("network unable to confirm new transactions" / node crash if this RPC front-end is co-located with sequencing/mempool ingestion services).

### Likelihood Explanation
High likelihood: the input is fully attacker-controlled (compressed program string), requires no special privileges, no prior state, and only one crafted RPC request. Gzip bombs achieve enormous compression ratios (kilobytes → gigabytes), making this trivially reproducible.

### Recommendation
Replace the unbounded `GzDecoder::read_to_end` call in `decompress_program` with the existing size-limited utility, e.g. use `flate2::read::GzDecoder` combined with `.take(max_size)` (as already implemented in `starknet_api::compression_utils::decompress_with_size_limit`), and enforce the same `max_contract_bytecode_size`/`max_contract_class_object_size` bounds used by the gateway's stateless validator before or during decompression. Add an explicit read timeout or step-limited decompression loop to also bound CPU/time.

### Proof of Concept
1. Construct a gzip-compressed JSON payload that decompresses to a very large size (e.g., a JSON array of millions of repeated tokens compressed to a few KB) — a classic "zip bomb."
2. Base64-encode the compressed bytes and place them in the `program` field of a `BroadcastedDeclareTransaction::V1` (deprecated Cairo-0 class) sent to an RPC method that triggers local conversion (e.g. `starknet_estimateFee` / `starknet_simulateTransactions` with the broadcasted declare transaction).
3. Observe the node's memory usage spike unbounded during `decompress_program`'s `read_to_end`, leading to OOM/crash or significant service disruption, since no size limit as in `decode_and_decompress_with_size_limit` is applied on this path.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L488-530)
```rust
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
