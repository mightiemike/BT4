## Analysis Result

### Title
Panic on class-manager/state-sync desync when class is declared but executable missing - (File: crates/apollo_gateway/src/sync_state_reader.rs)

### Summary
`SyncStateReader::get_contract_class_from_client` in the gateway's stateful state reader first asks the state-sync component whether a class hash is declared, then asks the (separate) class-manager component for the executable class body. If state-sync says "declared" but the class-manager returns `Ok(None)` for the same hash, the code does not propagate an error — it panics.

### Finding Description
`get_contract_class_from_client` performs two independent RPCs against two separate components: [1](#0-0) 

```rust
fn get_contract_class_from_client(&self, class_hash: ClassHash) -> StateResult<ContractClass> {
    let is_class_declared = self
        .runtime
        .block_on(self.state_sync_client.is_class_declared_at(self.block_number, class_hash))
        .map_err(|e| StateError::StateReadError(e.to_string()))?;

    if !is_class_declared {
        return Err(StateError::UndeclaredClassHash(class_hash));
    }

    let contract_class = self
        .runtime
        .block_on(self.class_manager_client.get_executable(class_hash))
        .map_err(|e| StateError::StateReadError(e.to_string()))?
        .unwrap_or_else(|| {
            panic!(
                "Class with hash {class_hash:?} doesn't appear in class manager even though \
                 it was declared"
            )
        });

    Ok(contract_class)
}
```

This is used by both `get_compiled_class` (invoked during transaction validation and execution) and `get_compiled_classes` [2](#0-1) .

The bug class mirrors the referenced celestia-node fix: a "success" response path (`get_executable` returning `Ok(None)` — no RPC error) is dereferenced as if it must contain data, causing a panic, instead of being converted into a recoverable error like `StateError::UndeclaredClassHash`. The existing test suite explicitly documents this as a panic path: [3](#0-2) 

`state_sync`'s notion of "declared" and the class-manager's storage of the executable class are two independently-populated data stores (state-sync tracks it via `is_cairo_1_class_declared_at`/deprecated class table lookups [4](#0-3) , while the class manager stores the executable body separately [5](#0-4) ). Nothing in the gateway/RPC path enforces atomicity between "class marked declared in state-sync storage" and "class body present in class manager storage" at the moment a transaction is validated.

### Impact Explanation
Any transaction (an ordinary `invoke`, `deploy_account`, or a call that references/reads a contract class) that is validated or executed against a class hash for which this inconsistency exists causes the gateway's state-reader thread to panic. Since this runs on a `spawn_blocking`/blocking-pool thread inside `runtime.block_on`, an uncaught panic here can propagate and crash the executing task, and repeated occurrence of the same condition (e.g., every retry of the same or similar transactions referencing that class) causes the gateway to keep failing to process transactions referencing the affected class hash. In the worst case this is a transaction-triggered denial-of-service against the sequencer's transaction admission pipeline — the node becomes unable to validate/confirm transactions calling contracts of that class until the underlying data inconsistency is manually resolved/restarted.

### Likelihood Explanation
This requires only a normal, unprivileged transaction that references a contract class whose declaration has been recorded by state-sync but whose executable body hasn't been (yet, or ever) persisted by the class manager. Given state-sync and class-manager are updated independently (see `is_class_declared_at` vs `get_executable`), timing gaps, restarts, or partial persistence of the class manager relative to state-sync's block marker are plausible in production without any malicious actor — any user transaction touching the affected class hash reliably triggers the panic once the inconsistency exists.

### Recommendation
Replace `.unwrap_or_else(|| panic!(...))` with a recoverable error, e.g. return `Err(StateError::UndeclaredClassHash(class_hash))` (as the sibling code path in `crates/apollo_rpc_execution/src/state_reader.rs` already does via `.ok_or(StateError::UndeclaredClassHash(class_hash))?` [6](#0-5) ), and log the anomaly instead of crashing the process.

### Proof of Concept
1. Have state-sync's storage record a class hash as declared at block N (e.g., via `is_class_declared_at`/deprecated-class marker) while the class manager component has not (yet, or due to data loss) stored the corresponding `get_executable` result for that class hash.
2. Submit any ordinary transaction (invoke/declare/deploy_account) whose validation or execution path calls `get_compiled_class`/`get_compiled_classes` for that class hash through `SyncStateReader`.
3. `get_contract_class_from_client` observes `is_class_declared == true` and `get_executable() == Ok(None)`, hitting the `unwrap_or_else(|| panic!(...))` branch, crashing the state-reader task instead of returning `StateError::UndeclaredClassHash`. [3](#0-2)

### Citations

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L77-99)
```rust
    fn get_contract_class_from_client(&self, class_hash: ClassHash) -> StateResult<ContractClass> {
        let is_class_declared = self
            .runtime
            .block_on(self.state_sync_client.is_class_declared_at(self.block_number, class_hash))
            .map_err(|e| StateError::StateReadError(e.to_string()))?;

        if !is_class_declared {
            return Err(StateError::UndeclaredClassHash(class_hash));
        }

        let contract_class = self
            .runtime
            .block_on(self.class_manager_client.get_executable(class_hash))
            .map_err(|e| StateError::StateReadError(e.to_string()))?
            .unwrap_or_else(|| {
                panic!(
                    "Class with hash {class_hash:?} doesn't appear in class manager even though \
                     it was declared"
                )
            });

        Ok(contract_class)
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L102-123)
```rust
impl FetchCompiledClasses for SyncStateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses> {
        let contract_class = self.get_contract_class_from_client(class_hash)?;
        match contract_class {
            ContractClass::V1(casm_contract_class) => {
                let sierra = self.read_sierra(class_hash)?.ok_or_else(|| {
                    error!(
                        "Class hash {class_hash:?} is declared in CASM but not in Sierra. Even \
                         though it should be coupled."
                    );
                    StateError::UndeclaredClassHash(class_hash)
                })?;
                Ok(CompiledClasses::V1(
                    CompiledClassV1::try_from(casm_contract_class)?,
                    Arc::new(sierra),
                ))
            }
            ContractClass::V0(deprecated_contract_class) => {
                Ok(CompiledClasses::V0(CompiledClassV0::try_from(deprecated_contract_class)?))
            }
        }
    }
```

**File:** crates/apollo_gateway/src/sync_state_reader_test.rs (L267-279)
```rust
#[tokio::test]
#[should_panic(expected = "Class with hash ClassHash(0x2) doesn't appear in class manager even \
                           though it was declared")]
async fn test_get_compiled_class_panics_when_class_exists_in_sync_but_not_in_class_manager() {
    test_get_compiled_class(
        Ok(None),
        1,
        Ok(true),
        Err(StateError::UndeclaredClassHash(*DUMMY_CLASS_HASH)),
        *DUMMY_CLASS_HASH,
    )
    .await;
}
```

**File:** crates/apollo_state_sync/src/lib.rs (L316-337)
```rust
    async fn is_class_declared_at(
        &self,
        block_number: BlockNumber,
        class_hash: ClassHash,
    ) -> StateSyncResult<bool> {
        if self.is_cairo_1_class_declared_at(block_number, class_hash).await? {
            return Ok(true);
        }

        let storage_reader = self.storage_reader.clone();
        // TODO(noamsp): Add unit testing for cairo0
        let deprecated_class_definition_block_number_opt = storage_reader
            .begin_ro_txn()?
            .get_state_reader()?
            .get_deprecated_class_definition_block_number(&class_hash)?;

        Ok(deprecated_class_definition_block_number_opt.is_some_and(
            |deprecated_class_definition_block_number| {
                deprecated_class_definition_block_number <= block_number
            },
        ))
    }
```

**File:** crates/apollo_class_manager/src/communication.rs (L52-57)
```rust
            ClassManagerRequest::GetExecutable(class_id) => {
                ClassManagerResponse::GetExecutable(self.0.get_executable(class_id))
            }
            ClassManagerRequest::GetSierra(class_id) => {
                ClassManagerResponse::GetSierra(self.0.get_sierra(class_id))
            }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L115-119)
```rust
        if let Some((class_manager_client, run_time_handle)) = &self.class_manager_handle {
            let contract_class = run_time_handle
                .block_on(class_manager_client.get_executable(class_hash))
                .map_err(|e| StateError::StateReadError(e.to_string()))?
                .ok_or(StateError::UndeclaredClassHash(class_hash))?;
```
