### Title
Unbounded gzip decompression allocation in `decompress_program` allows remote memory-exhaustion DoS via `estimateFee`/`simulateTransactions`/`call` on a `DECLARE` v1 broadcast tx - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
`decompress_program` in `apollo_rpc` decompresses the gzip-compressed, base64-encoded Cairo0 `program` field of a *broadcasted* (not yet mined) `DECLARE` V1 transaction, with an explicit `// TODO(dan): add time and size limits.` comment indicating no bound is enforced. This function is reachable from unprivileged, unauthenticated JSON-RPC callers via any endpoint that accepts a `BroadcastedDeclareTransaction::V1` for simulation/execution (e.g. fee estimation, simulate, or trace/call endpoints), unlike the analogous, properly size-limited decompression path used for on-chain `DECLARE` submissions through the gateway.

### Finding Description
`decompress_program` reads a user-controlled base64 string, decodes it, and feeds it into a `GzDecoder`, then calls `decoder.read_to_end(&mut decompressed)` with **no cap** on the output size: [1](#0-0) 

This is invoked from `user_deprecated_contract_class_to_sn_api`, which is used when converting a `BroadcastedDeclareTransaction::V1` into an `ExecutableTransactionInput` for local RPC-side execution (simulate/estimate/call): [2](#0-1) [3](#0-2) 

This is exactly the bug class in the external report: a small, attacker-supplied compressed payload can be crafted (a "gzip bomb") to decompress into an enormous buffer (potentially gigabytes), forcing the node to allocate that memory in a single call before any subsequent class-size validation can reject it — because the size check happens only after decompression completes (`get_class_lengths` / abi-length calculations operate on the already-decompressed `Program`), not before or during decompression.

This contrasts with the codebase's own hardened pattern used elsewhere for compressed, user-submitted data: `decompress_with_size_limit` in `starknet_api::compression_utils`, which uses `.take(max_size + 1)` to bound the number of bytes read out of the decompressor before it ever grows past the limit, and returns an error if the limit is exceeded: [4](#0-3) 
That size-limited helper is correctly used for the gateway's on-chain-bound `DECLARE` path (`decode_and_decompress_with_size_limit`, called from `apollo_http_server`'s `DeprecatedGatewaySierraContractClass::convert_to_sierra_contract_class` and from `apollo_protobuf`'s Cairo0 class conversion with `MAX_CAIRO0_PROGRAM_SIZE`): [5](#0-4) [6](#0-5) 

`decompress_program`, however, is not gated by any equivalent bound, making it the unguarded analog.

### Impact Explanation
A single unauthenticated JSON-RPC request containing a crafted `BroadcastedDeclareTransaction::V1` with a highly-compressible `compressed_program` payload can force the RPC node process to allocate an attacker-chosen (effectively unbounded, gzip-ratio-limited but still very large, e.g. hundreds of MB to GB range from a tiny compressed input) buffer in memory. Repeated or concurrent requests can exhaust node memory, causing the process to be OOM-killed — a remote denial-of-service against the RPC/execution node, matching the impact class in the reference report ("a network unable to confirm new transactions" if this degrades or crashes sequencer/RPC availability for simulate/estimate calls used by wallets and other clients before submission).

### Likelihood Explanation
High likelihood of triggering: the attack requires no special privileges, no on-chain state, and no fee payment — it's a pure RPC call with a crafted request body. It can be automated and repeated cheaply, and the vulnerable code path is explicitly marked with a `TODO` acknowledging the missing limit, confirming the gap is a known but unaddressed issue in this exact function.

### Recommendation
Apply the same bounded-read pattern already used in `starknet_api::compression_utils::decompress_with_size_limit` to `decompress_program`: wrap the `GzDecoder` read with `.take(max_size + 1)` against a configured maximum (e.g. reuse `MAX_CAIRO0_PROGRAM_SIZE` or an RPC-config equivalent) and return an error if the decompressed size exceeds that bound, rather than calling unbounded `read_to_end`.

### Proof of Concept
1. Craft a `DECLARE` V1 broadcast transaction JSON body for `starknet_estimateFee` / `starknet_simulateTransactions` / `starknet_call`, with `contract_class.program` set to a small gzip-compressed, base64-encoded blob engineered as a decompression bomb (e.g. gzip of a highly repetitive multi-hundred-MB string, compressing to a few KB).
2. Submit the request to the RPC node's `starknet_estimateFee` (or `simulateTransactions`) endpoint — no signature validity or funds are required since this happens at the `TryFrom<BroadcastedDeclareTransaction>` conversion stage before/independent of full transaction validation.
3. Observe the node allocate the full decompressed size in `decompress_program`'s `let mut decompressed = Vec::new(); decoder.read_to_end(&mut decompressed)` call — repeat concurrently to trigger memory exhaustion / OOM kill of the RPC process.

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

**File:** crates/apollo_protobuf/src/converters/class.rs (L131-137)
```rust
        let abi = serde_json::from_str(&value.abi)?;
        // TODO(dan): use config for this.
        const MAX_CAIRO0_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
        let program =
            decode_and_decompress_with_size_limit(&value.program, MAX_CAIRO0_PROGRAM_SIZE)?;

        Ok(Self { program, entry_points_by_type, abi })
```
