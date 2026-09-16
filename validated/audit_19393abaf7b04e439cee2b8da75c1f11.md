### Title
Unbounded gzip decompression of attacker‑supplied Cairo0 program in `decompress_program` enables a memory‑exhaustion DoS via `starknet_estimateFee`/`starknet_simulateTransactions` - ([File: crates/apollo_rpc/src/v0_8/api/mod.rs])

### Summary
The `lnd-onion-bomb` report describes a DoS where a small, attacker-controlled payload is decompressed/parsed into an unbounded, memory-exhausting allocation before any validation gate. The same bug class exists in the sequencer's RPC execution layer: `decompress_program` decompresses the base64-encoded gzip `compressed_program` field of a Cairo0 (`DeclareV1`) `BroadcastedDeclareTransaction` with **no size or time limit**, unlike every other decompression path in the codebase (`decode_and_decompress_with_size_limit`), which is explicitly bounded.

### Finding Description
`decompress_program` in `apollo_rpc/src/v0_8/api/mod.rs` decodes and gunzips an attacker-supplied string with an unbounded `Vec::new()` sink and `read_to_end`: [1](#0-0) 

This is explicitly flagged by its own TODO ("add time and size limits"), and it is the *only* decompression helper in the codebase lacking the size-limited wrapper used everywhere else (`decompress_with_size_limit` / `decode_and_decompress_with_size_limit`): [2](#0-1) 

This unbounded path is reached when a caller submits a `BroadcastedDeclareTransaction::V1` (deprecated Cairo0 declare) to `starknet_estimateFee` or `starknet_simulateTransactions`. These JSON-RPC methods convert the broadcast transaction into an `ExecutableTransactionInput::DeclareV1` and then locally *execute* it (unlike `addDeclareTransaction`, which merely forwards to a writer client without local execution): [3](#0-2) [4](#0-3) 

The test `get_decompressed_program` and `broadcasted_to_executable_declare_v1` confirm `decompress_program` is on this conversion path for Cairo0 declare classes: [5](#0-4) 

Compare this to the properly-hardened, size-limited decompression used for the same kind of data elsewhere (deprecated-class decompression via protobuf sync, and Sierra program decompression in the deprecated gateway path), both of which cap output size before allocating: [6](#0-5) [7](#0-6) 

### Impact Explanation
A client can submit a tiny gzip payload (few KB, e.g. all-zero data compresses to a huge ratio) as `compressed_program` in a `DeclareV1` broadcast transaction to `starknet_estimateFee` / `starknet_simulateTransactions`. `GzDecoder::read_to_end` will attempt to fully materialize the decompressed output with no bound, causing the RPC/full-node process to allocate gigabytes of memory and potentially OOM-crash or severely degrade the service — a classic decompression-bomb DoS, directly analogous to the LND onion bomb's unbounded-allocation-from-untrusted-input pattern. Repeated concurrent requests amplify the effect trivially since the request body itself is tiny.

### Likelihood Explanation
High: the endpoint is a standard, unauthenticated JSON-RPC read-API method (`estimateFee`/`simulateTransactions`), requires no fees, no valid signature, no prior on-chain state, and no special privilege — any external caller of the node's RPC endpoint can trigger it with a single request.

### Recommendation
Route `decompress_program` through the existing `decode_and_decompress_with_size_limit` helper (or equivalent bounded `Read::take`), enforcing a maximum decompressed program size consistent with the codebase's `max_contract_bytecode_size` / `DEFAULT_MAX_SIERRA_PROGRAM_SIZE` conventions, and reject/`PAYLOAD_TOO_LARGE` early rather than after full decompression.

### Proof of Concept
1. Craft a `DeclareV1` `BroadcastedDeclareTransaction` whose `contract_class.compressed_program` is a base64-encoded gzip blob of a few KB that decompresses to several GB (e.g., all-zero bytes, which gzip compresses at extremely high ratios).
2. Send it as part of the `request` array to `starknet_estimateFee` (or `starknet_simulateTransactions`) on the node's public RPC endpoint.
3. `decompress_program` (`crates/apollo_rpc/src/v0_8/api/mod.rs:671-682`) allocates and grows a `Vec<u8>` without bound while decompressing, exhausting node memory before any subsequent validation/execution step runs.

**Uncertainty note:** I could not fully trace whether the `apollo_rpc` full-node RPC server sits behind an independent request-body size limiter (as `apollo_http_server` does for the gateway ingestion path) that would bound the *outer* JSON-RPC request size; even if so, that would only cap the *compressed* size, not the unbounded *decompressed* output, which is the actual bug. I was not able to fully confirm within index limits whether this `apollo_rpc` service is considered part of the "sequencer" scope per the task's strict definition (it appears to be a full/read-node RPC crate rather than the core gateway/mempool/blockifier sequencing pipeline); this should be verified against deployment topology before triage.

### Citations

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L478-509)
```rust
                .get_block_transactions_count(block_number)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(BLOCK_NOT_FOUND))?)
        }
    }

    #[instrument(skip(self), level = "debug", err, ret)]
    async fn get_state_update(&self, block_id: BlockId) -> RpcResult<StateUpdate> {
        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;
        if let BlockId::Tag(Tag::Pending) = block_id {
            let state_update = read_pending_data(&self.pending_data, &txn).await?.state_update;
            return Ok(StateUpdate::PendingStateUpdate(PendingStateUpdate {
                old_root: state_update.old_root,
                state_diff: state_update.state_diff.into(),
            }));
        }

        // Get the block header for the block hash and state root.
        let block_number = get_accepted_block_number(&txn, block_id)?;
        let header: BlockHeader = get_block_header_by_number(&txn, block_number)?.into();

        // Get the old root.
        let old_root = match get_accepted_block_number(
            &txn,
            BlockId::HashOrNumber(BlockHashOrNumber::Hash(header.parent_hash)),
        ) {
            Ok(parent_block_number) => {
                BlockHeader::from(get_block_header_by_number(&txn, parent_block_number)?).new_root
            }
            Err(_) => GlobalRoot::ROOT_OF_EMPTY_STATE,
        };

```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L997-1048)
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
```

**File:** crates/apollo_rpc/src/v0_8/execution_test.rs (L1340-1345)
```rust
#[test]
fn get_decompressed_program() {
    let compressed = get_test_compressed_program();
    let decompressed = decompress_program(&compressed);
    decompressed.expect("Couldn't decompress program");
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
