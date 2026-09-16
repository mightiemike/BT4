### Title
Unbounded persistent disk consumption via declare transactions that fail fee/stateful validation — ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
In the gateway's transaction-admission pipeline, a `Declare` transaction's Sierra class is compiled and **permanently written to the class manager's persistent filesystem storage before fee, nonce, and balance validation (stateful validation) is performed**. Because a rejected declare (e.g., insufficient balance, invalid nonce, resource-bounds too low) never reaches the mempool/block, the sender pays nothing and the class is never garbage-collected, yet the compiled Sierra+CASM artifacts remain on disk forever. A permitted declarer can repeat this with distinct contract content (unique class hash each time) to grow disk usage without bound, at effectively zero cost, since the write happens irrespective of whether the transaction is ultimately admitted.

### Finding Description
The gateway's `add_tx_inner` performs, in order:
1. `check_declare_permissions` (allow-list check) and `stateless_tx_validator.validate` (format/signature only).
2. `convert_rpc_tx_to_internal_and_executable_txs`, which calls `self.transaction_converter.convert_rpc_tx_to_internal_rpc_tx(tx)`. [1](#0-0) 

Inside the converter, for a `Declare` transaction, the Sierra class is unconditionally sent to the class manager and compiled/persisted **before any fee, nonce, or balance check**: [2](#0-1) 

`ClassManager::add_class` compiles the Sierra to CASM and calls `self.classes.set_class(...)`, which persists both the Sierra and executable class to disk via `FsClassStorage`: [3](#0-2) [4](#0-3) 

The actual on-disk write is atomic and content-addressed by class hash, and there is no path that deletes a class that was written but whose owning transaction was subsequently rejected: [5](#0-4) 

Only **after** this disk write does the gateway run stateful validation (nonce, resource bounds/fee, balance): [6](#0-5) [7](#0-6) 

If stateful validation fails, `add_tx_inner` returns an error and the transaction never reaches the mempool — the class artifacts already written to `FsClassStorage`'s `persistent_root` are never removed. The only class-cleanup code paths in the codebase operate on **block reverts** of already-committed state diffs (`delete_declared_classes`, `delete_compiled_classes` in `apollo_storage`), which is a completely different storage tier (the block-indexed `apollo_storage`, not the class manager's `FsClassStorage`) and is irrelevant to declare-only rejections at the gateway: [8](#0-7) 

The only mitigating control is `max_concurrent_declare_compilations` (a semaphore bounding parallelism) and `max_compiled_contract_class_object_size` (a per-class size cap), neither of which bounds the *cumulative* number of distinct classes persisted over time: [9](#0-8) [10](#0-9) 

### Impact Explanation
An account permitted to submit declare transactions (gated only by `is_authorized_declarer`, a coarse allow-list check performed with no state/balance knowledge) can submit an unbounded stream of syntactically distinct Sierra classes (e.g., varying an unused literal) each attached to a declare transaction engineered to fail stateful validation (e.g., insufficient balance/resource bounds, or a stale/invalid nonce). Each attempt:
- Consumes CPU/memory to compile Sierra→CASM.
- Permanently writes the Sierra and CASM to the class manager's disk-backed store, up to `max_compiled_contract_class_object_size` (default ~4 MB) per class.
- Is rejected before reaching the mempool, so the sender incurs no on-chain fee and the class is never referenced by a committed state diff (so it will never be cleaned up by any revert path).

This causes uncontrolled, permanent growth of the class-manager node's disk, eventually exhausting storage and causing the sequencer (or class-manager service) to fail — a denial of service, directly analogous to the original Nova CVE-2015-3280 pattern where a resource (disk) is consumed by an authenticated action whose associated instance/artifact is never properly cleaned up on the failure/deletion path.

### Likelihood Explanation
Any account satisfying `is_authorized_declarer` can trigger this repeatedly and cheaply — the disk write occurs before the costly fee/balance validation, so the attacker does not need sufficient funds to actually pay for the declare, only enough to pass stateless (format/signature) checks. The only throttle is a global compilation concurrency semaphore, which limits parallelism but not the total number of sequential attempts over time. This makes the bug class straightforwardly and repeatedly exploitable by a single permitted sender, requiring no chain state manipulation or race conditions.

### Recommendation
Defer persisting the compiled class to the class manager's durable storage until after stateful validation (nonce/fee/balance) succeeds, or introduce a bounded, evictable staging cache for classes pending admission that is only promoted to permanent storage once the declare transaction is confirmed admissible (and is garbage-collected on rejection/timeout). Additionally, consider charging/reserving fee before compilation+persistence, and enforce a hard cap on aggregate bytes/number of classes persisted per unresolved declarer within a time window.

### Proof of Concept
1. Ensure the attacker account is on the `is_authorized_declarer` allow-list (or the deployment configures it permissively).
2. Submit an RPC `Declare` (V3) transaction with a valid signature/format but with `resource_bounds` set below what the account's balance can cover (or an intentionally-stale `nonce`), and a Sierra `contract_class` containing unique/novel bytecode (to yield a fresh class hash) sized near `max_compiled_contract_class_object_size`.
3. Observe: `add_tx` calls `convert_rpc_tx_to_internal_and_executable_txs` → `class_manager_client.add_class(contract_class)`, which compiles and persists the class to `FsClassStorage::persistent_root` on disk (verifiable via the class manager's data directory).
4. Observe that subsequent stateful validation (`extract_state_nonce_and_run_validations`) fails and `add_tx` returns an error to the caller; the transaction never reaches the mempool.
5. Repeat steps 2–4 with new unique class content; disk usage under the class-manager's persistent root grows monotonically with no corresponding successful declare transactions or committed state diffs referencing these classes.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L136-138)
```rust
    // Bounds the number of concurrent Sierra-to-CASM compilations triggered by declare
    // transactions. Shared across all clones of the gateway so the limit is process-global.
    declare_compilation_semaphore: Arc<Semaphore>,
```

**File:** crates/apollo_gateway/src/gateway.rs (L235-255)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();

        // Declare conversions overload the compiler component's CPU and memory. Reject declares if
        // there are too many declares compiling in parallel. The permit is held only across
        // compilation and released before stateful validation.
        let compilation_permit = if matches!(tx, RpcTransaction::Declare(_)) {
            Some(self.declare_compilation_semaphore.try_acquire().map_err(|_| {
                let error = StarknetError::too_many_concurrent_declare_compilations();
                metric_counters.record_add_tx_failure(&error);
                error
            })?)
        } else {
            None
        };

        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);
```

**File:** crates/apollo_gateway/src/gateway.rs (L257-266)
```rust
        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-360)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-113)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }

        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
        debug!(
            %class_hash,
            compiled_class_hash = %executable_class_hash_v2,
            compilation_elapsed_ms = compilation_start_time.elapsed().as_millis(),
            class_size_bytes =
                class.size().map_or("Failed to get class size".to_owned(), |size| size.to_string()),
            casm_size_bytes =
                raw_executable_class.size().map_or("Failed to get casm size".to_owned(), |size| size.to_string()),
            "Finished compiling class."
        );

        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;

        let class_hashes = ClassHashes { class_hash, executable_class_hash_v2 };
        Ok(class_hashes)
    }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L157-179)
```rust
    fn validate_class_length(
        &self,
        serialized_class: &RawExecutableClass,
    ) -> ClassManagerResult<()> {
        // Note: The class bytecode length is validated in the compiler.

        let contract_class_object_size =
            serialized_class.size().expect("Unexpected error serializing contract class.");
        if contract_class_object_size
            > self.config.static_config.class_manager_config.max_compiled_contract_class_object_size
        {
            return Err(ClassManagerError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self
                    .config
                    .static_config
                    .class_manager_config
                    .max_compiled_contract_class_object_size,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L451-463)
```rust
    fn write_class_atomically(
        &self,
        class_id: ClassId,
        class: RawClass,
        executable_class: RawExecutableClass,
    ) -> FsClassStorageResult<()> {
        // Write classes to a temporary directory.
        let (_tmp_root, tmp_dir) = self.create_tmp_dir(class_id)?;
        class.write_to_file(concat_sierra_filename(&tmp_dir))?;
        executable_class.write_to_file(concat_executable_filename(&tmp_dir))?;

        self.rename_to_persistent_dir(tmp_dir, class_id)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L503-522)
```rust
impl ClassStorage for FsClassStorage {
    type Error = FsClassStorageError;

    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.contains_class(class_id)? {
            return Ok(());
        }

        self.write_class_atomically(class_id, class, executable_class)?;
        self.mark_class_id_as_existent(class_id, executable_class_hash_v2)?;

        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L213-221)
```rust
    async fn validate_state_preconditions(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
    ) -> StatefulTransactionValidatorResult<()> {
        self.validate_resource_bounds(executable_tx).await?;
        self.validate_nonce(executable_tx, account_nonce)?;
        Ok(())
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L742-766)
```rust
        let deleted_classes = delete_declared_classes(
            &self.txn,
            &thin_state_diff,
            &declared_classes_table,
            &self.file_handlers,
        )?;
        let deleted_deprecated_class_hashes = delete_deprecated_declared_classes_block(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deprecated_declared_classes_block_table,
        )?;
        let deleted_deprecated_classes = delete_deprecated_declared_classes(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deprecated_declared_classes_table,
            &self.file_handlers,
        )?;
        let deleted_compiled_classes = delete_compiled_classes(
            &self.txn,
            thin_state_diff.class_hash_to_compiled_class_hash.keys(),
            &compiled_classes_table,
            &self.file_handlers,
        )?;
```
