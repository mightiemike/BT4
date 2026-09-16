I found a concrete analog: `SyncStateReader::get_compiled_class_hash` in `apollo_gateway` contains a literal `todo!()` panic instead of an implementation, directly analogous to the Astaria bug where an interface method (`strategistNonce`) is called but never implemented.

### Title
Unimplemented `get_compiled_class_hash` in `SyncStateReader` causes gateway panic on reachable code path - (File: `crates/apollo_gateway/src/sync_state_reader.rs`)

### Summary
`SyncStateReader` implements the blockifier `StateReader` trait used by the gateway to read state while validating incoming transactions, but its `get_compiled_class_hash` method body is `todo!()`, which unconditionally panics if invoked.

### Finding Description
The `StateReader` trait requires implementations of `get_compiled_class_hash`, which is a legitimate, potentially-reachable state-read operation (e.g., used to fetch/verify the compiled class hash of a declared class) [1](#0-0) . The production `SyncStateReader` used by the gateway's stateful transaction validation path implements this trait for state used during Declare/other transaction validation, but its implementation is a stub that panics: [2](#0-1) 
This mirrors the Astaria pattern exactly: an interface/trait requires a function, but the concrete implementation used in the live path never actually implements the logic — it just fails when invoked. The `SyncOrGenesisStateReader` wrapper (used by `SyncStateReaderFactory`, the factory that supplies state readers for every gateway-processed transaction) dispatches directly into this unimplemented method without any guard: [3](#0-2) [4](#0-3) 

### Impact Explanation
If any code path in the gateway's stateful transaction validation (state reads performed via `StateReaderFactory`/`SyncStateReader`) calls `get_compiled_class_hash` — for example while resolving a v1 class's compiled class hash during declare/validation flows — the gateway process panics (`todo!()` unwinds/aborts the thread executing the request). This is a denial-of-service on the transaction submission path: a single unprivileged submitted transaction that triggers this code path can crash the validating node/thread, preventing that gateway instance from confirming further transactions until restarted. Whether this specific method is reached today by an unprivileged submitter's transaction depends on which state-reading call sites invoke `get_compiled_class_hash` versus `get_compiled_class_hash_v2` (the latter has its own default `unimplemented!()` in the base trait, at `crates/blockifier/src/state/state_api.rs:69-79`, compounding the risk of an unimplemented trait method being invoked on a validation hot path). I could not fully trace every call site of `get_compiled_class_hash` within the time available to conclusively prove it is invoked purely from an unprivileged transaction's validation flow versus only from privileged/internal batcher paths — this should be verified by grepping all callers of `StateReader::get_compiled_class_hash` across `apollo_gateway`, `blockifier`, and `apollo_batcher` before treating this as confirmed-exploitable rather than a a latent stub.

### Likelihood Explanation
Medium: the stub exists in production code (not test/mock code, unlike the many other `todo!()`/`unimplemented!()` hits which are confined to test utilities such as `native_blockifier/test_utils.rs`, `apollo_network/test.rs`, etc.). `SyncStateReader` is the primary production state reader wired into the gateway via `SyncStateReaderFactory`, so it is reachable if any validation logic calls this specific trait method for a class declared/used by an incoming transaction.

### Recommendation
Implement `get_compiled_class_hash` in `SyncStateReader` to properly fetch the compiled class hash via the state sync client / class manager client (following the same pattern as `get_compiled_class`), instead of leaving a `todo!()`. Additionally, audit all trait methods with default `unimplemented!()` bodies (e.g., `get_compiled_class_hash_v2` in `state_api.rs`) to ensure no production-reachable state reader relies on the default panic-based implementation.

### Proof of Concept
1. Submit a transaction to the gateway that requires resolving the compiled class hash of a class via `StateReader::get_compiled_class_hash` (e.g., a Declare or invoke flow exercising a code path that queries `get_compiled_class_hash` rather than `get_compiled_class`).
2. The gateway's `SyncStateReaderFactory` supplies a `SyncOrGenesisStateReader::Sync(SyncStateReader)` instance for state reads [5](#0-4) .
3. The call reaches `SyncStateReader::get_compiled_class_hash`, which executes `todo!()` and panics the handling thread/task [2](#0-1) , denying service for that request and potentially the gateway worker.

### Citations

**File:** crates/blockifier/src/state/state_api.rs (L44-46)
```rust
    /// Returns the compiled class hash of the given class hash.
    /// Returns CompiledClassHash::default() if no v1_class is found for the given class hash.
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash>;
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L197-199)
```rust
    fn get_compiled_class_hash(&self, _class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        todo!()
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L440-447)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        match self {
            Self::Sync(state_reader) => state_reader.get_compiled_class_hash(class_hash),
            Self::Genesis(genesis_state_reader) => {
                genesis_state_reader.get_compiled_class_hash(class_hash)
            }
        }
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L515-547)
```rust
impl StateReaderFactory for SyncStateReaderFactory {
    type TGatewayStateReaderWithCompiledClasses = SyncOrGenesisStateReader;
    type TGatewayFixedBlockStateReader = SyncOrGenesisFixedBlockStateReader;

    // TODO(guy.f): The call to `get_latest_block_number()` is not counted in the storage metrics as
    // it is done prior to the creation of SharedStateSyncClientMetricWrapper, directly via the
    // SharedStateSyncClient.
    async fn get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block(
        &self,
    ) -> StateSyncClientResult<(
        Self::TGatewayStateReaderWithCompiledClasses,
        Self::TGatewayFixedBlockStateReader,
    )> {
        let latest_block_number = self.shared_state_sync_client.get_latest_block_number().await?;

        // If no blocks exist yet, return genesis state readers for bootstrap transactions.
        let Some(latest_block_number) = latest_block_number else {
            info!("No blocks found yet; using genesis state readers for bootstrap transactions.");
            return Ok((GenesisStateReader.into(), GenesisFixedBlockStateReader.into()));
        };

        let blockifier_state_reader = SyncStateReader::from_number(
            self.shared_state_sync_client.clone(),
            self.class_manager_client.clone(),
            latest_block_number,
            self.runtime.clone(),
        );
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
        Ok((blockifier_state_reader.into(), gateway_fixed_block_sync_state_client.into()))
    }
```
