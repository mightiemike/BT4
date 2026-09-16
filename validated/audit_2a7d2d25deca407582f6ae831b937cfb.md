## Finding: Unbounded Gzip Decompression of User-Supplied Declare V1 Contract Program — Memory Exhaustion DoS

### Title
Uncontrolled Resource Consumption via Unbounded Gzip Decompression in `decompress_program` - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The RPC-exposed conversion path for legacy Cairo0 `DECLARE` (v1) transactions decompresses a client-supplied, base64+gzip-encoded `compressed_program` field with no cap on the decompressed size or time, directly mirroring the Apache NiFi CVE-2026-68981 bug class (decompression bomb via a size-unconstrained request field). Every other decompression path in this codebase (`apollo_http_server`'s gateway ingestion, `starknet_api::compression_utils::decode_and_decompress_with_size_limit`) enforces an explicit `max_size` bound; this one does not, and is even marked with `// TODO(dan): add time and size limits.`

### Finding Description
`decompress_program` in `crates/apollo_rpc/src/v0_8/api/mod.rs:671-682` gzip-decompresses arbitrary client-controlled bytes with `GzDecoder::read_to_end`, which reads until EOF with no output-size limit: [1](#0-0) 

It is called from `user_deprecated_contract_class_to_sn_api`, which converts a `BroadcastedDeclareV1Transaction`'s user-supplied `contract_class.compressed_program` field: [2](#0-1) 

`BroadcastedDeclareV1Transaction` (a Cairo0 declare transaction) is a top-level, unauthenticated input struct constructed straight from client JSON in the `DECLARE` variant of `BroadcastedTransaction`: [3](#0-2) 

`BroadcastedTransaction::Declare` flows into `ExecutableTransactionInput` via a `TryFrom` conversion used by `estimate_fee`, `estimate_message_fee`-adjacent `simulate_transactions`, and `add_declare_transaction`, all reachable via the public JSON-RPC methods `starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_addDeclareTransaction`: [4](#0-3) [5](#0-4) 

This is a strict regression relative to the codebase's own established pattern for handling compressed, client-supplied program data. The gateway's HTTP ingestion path enforces a hard decompressed-byte limit specifically to defend against zip bombs: [6](#0-5) 

And the shared `starknet_api` crate's decompression helper used for Sierra programs explicitly caps decompressed output via a `Take` adapter and rejects oversized results: [7](#0-6) 

`decompress_program`, however, has no such bound — it is the only decompression sink in the codebase that reads a client-controlled gzip stream to completion unconditionally, exactly the pattern the NiFi advisory describes (limit enforced on the compressed/wire size but not on the decompressed output).

### Impact Explanation
A single unauthenticated JSON-RPC call (`starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_addDeclareTransaction`) carrying a `DECLARE` v1 transaction with a small but highly-compressible `compressed_program` (a classic gzip "zip bomb," e.g., a few KB compressing to gigabytes of zeros) forces the node to allocate unbounded memory in `decoder.read_to_end(&mut decompressed, ...)`. This can exhaust process memory and crash or severely degrade the RPC-serving node, denying legitimate `estimateFee`/`simulateTransactions`/`addDeclareTransaction` service — a resource-consumption DoS matching the "network unable to confirm new transactions" impact class when this component is relied upon for transaction submission/estimation flows.

### Likelihood Explanation
High. The attack requires no privileges, no fee payment (the vulnerable decompression happens during input conversion, before fee/signature validation), and no special network position — only a single crafted RPC request with a well-known, trivially-constructed gzip bomb payload.

### Recommendation
Apply the same bounded-decompression pattern already used elsewhere in the codebase (`decode_and_decompress_with_size_limit` in `starknet_api::compression_utils`) to `decompress_program`: wrap the `GzDecoder` in a size-limited reader (e.g., `.take(max_size + 1)`) and reject the input if the decompressed length exceeds a configured maximum, returning an RPC error instead of unbounded allocation. Also consider adding a decompression time bound as the existing TODO already flags.

### Proof of Concept
1. Generate a gzip payload of a few kilobytes that decompresses to several hundred MB/GB (e.g., `gzip -9` a large all-zero file).
2. Base64-encode the compressed bytes and place them in `contract_class.compressed_program` of a `BroadcastedDeclareV1Transaction`.
3. Send this as the `request` parameter of `starknet_estimateFee` (or `starknet_simulateTransactions` / `starknet_addDeclareTransaction`) to the RPC node.
4. Observe `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) allocate the full decompressed size in memory with no limit check, causing excessive memory consumption / OOM.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L222-228)
```rust
    #[method(name = "estimateFee")]
    async fn estimate_fee(
        &self,
        request: Vec<BroadcastedTransaction>,
        simulation_flags: Vec<SimulationFlag>,
        block_id: BlockId,
    ) -> RpcResult<Vec<FeeEstimation>>;
```

**File:** crates/apollo_rpc/src/v0_8/api/mod.rs (L329-339)
```rust
impl TryFrom<BroadcastedTransaction> for ExecutableTransactionInput {
    type Error = ErrorObjectOwned;
    fn try_from(value: BroadcastedTransaction) -> Result<Self, Self::Error> {
        // TODO(yair): pass the right value for only_query field.
        match value {
            BroadcastedTransaction::Declare(tx) => Ok(tx.try_into()?),
            BroadcastedTransaction::DeployAccount(tx) => Ok(Self::DeployAccount(tx.into(), false)),
            BroadcastedTransaction::Invoke(tx) => Ok(Self::Invoke(tx.into(), false)),
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

**File:** crates/apollo_http_server/src/http_server.rs (L145-152)
```rust
            .layer(Extension(self.app_state.clone()))
            // Hard streaming limit on decompressed bytes — wraps the body in
            // http_body_util::Limited which errors during poll_frame() once the
            // limit is exceeded, preventing zip bombs from expanding in memory.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
            .layer(RequestDecompressionLayer::new())
            // Cap compressed wire bytes to bound network I/O.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
```
