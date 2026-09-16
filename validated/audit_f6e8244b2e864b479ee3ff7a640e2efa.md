### Title
Unpaid-Sender Disk-Fill DoS via Permanent Class Storage Before Fee/Balance Validation - (File: crates/apollo_gateway/src/gateway.rs)

### Summary
`GenericGateway::add_tx_inner` compiles a `Declare` transaction's Sierra class to CASM and persists both to disk via `ClassManager::add_class` / `FsClassStorage::set_class` *before* the sequencer ever checks whether the sender can pay for the declaration (balance/fee, valid nonce, valid signature). Any remote, unauthenticated party can therefore force the sequencer to durably write arbitrary-sized (up to the configured max) Sierra+CASM pairs to disk for transactions that are guaranteed to be rejected, with no cleanup path for classes that never make it into a committed block. This mirrors the Fake Stake CVE-2018-19157 pattern — unvalidated attacker-supplied data reaching persistent storage before validation completes.

### Finding Description
The transaction admission flow in `add_tx_inner` is:
1. `stateless_tx_validator.validate(&tx)` — only checks structural bounds (signature length, calldata length, resource-bounds non-zero, contract-class object/bytecode size limits). It performs **no** signature verification and **no** balance/fee check. [1](#0-0) [2](#0-1) 

2. Immediately after, for `Declare` transactions, `convert_rpc_tx_to_internal_and_executable_txs` is invoked, which drives Sierra→CASM compilation and persistent storage via the class manager, all **before** `extract_state_nonce_and_run_validations` (the stateful validator that reads account nonce, checks balance/fee, and runs the real `__validate__` entry point / signature check) is ever called: [3](#0-2) 

3. `ClassManager::add_class` compiles the class and calls `self.classes.set_class(...)`, which writes the Sierra and CASM to the filesystem unconditionally (deduplicated only by class hash): [4](#0-3) [5](#0-4) 

4. `FsClassStorage::write_class_atomically` performs the actual disk write (Sierra + executable files) into a content-addressed persistent directory: [6](#0-5) 

5. Only after this data is durably persisted does the gateway run `extract_state_nonce_and_run_validations`, which performs the nonce/balance/signature checks that would reject a spam declare from an account with no funds or an invalid signature: [7](#0-6) [8](#0-7) 

The only guardrail present is a semaphore limiting *concurrent* compilations to bound CPU/memory during compilation itself; it is explicitly documented as protecting only compilation resources, not the resulting persisted artifact, and is released once compilation finishes — long before validation: [9](#0-8) [10](#0-9) 

There is no eviction/TTL/garbage-collection mechanism for classes stored via `FsClassStorage::set_class` that never end up declared in a committed block. The only deletion path found (`delete_declared_classes` in `apollo_storage`) removes classes from the *block storage* on chain reorg/revert, not from the class-manager's `FsClassStorage`, and only applies to classes that were actually included in a state diff: [11](#0-10) 

An attacker does not need funds or a valid account: they only need to submit a syntactically valid Sierra program (up to `max_contract_bytecode_size` = 81920 bytes and `max_contract_class_object_size` = 4089446 bytes) that produces a unique class hash, targeting any sender address (even one with zero balance/no deployed account). The declare will pass stateless validation, get compiled and permanently written to disk, and only then fail stateful validation (e.g., `InsufficientAccountBalance` or invalid nonce/signature). Each unique Sierra program yields a unique class hash, so dedup in `set_class`/`contains_class` does not prevent repeated writes. [12](#0-11) 

### Impact Explanation
This allows a remote, unauthenticated (or trivially funded — since no fee is actually charged for rejected transactions) attacker to continuously fill the sequencer's disk with attacker-chosen, near-maximum-size class artifacts (Sierra ~4MB class object + compiled CASM) at a rate limited only by the concurrent-compilation semaphore, not by any per-account quota or cleanup of orphaned data. Sustained disk growth on the class-manager's persistent storage volume can exhaust disk space, causing the sequencer (and any node relying on the same storage, e.g. in consolidated deployments) to become unable to accept new declares, and in the worst case unable to write new blocks/state at all once the disk is full — a network-wide liveness/availability impact matching "network unable to confirm new transactions."

### Likelihood Explanation
High. No signature, balance, or account existence is required to reach the vulnerable code path — only a structurally valid Sierra class passing size limits. The `declare_compilation_semaphore` throttles concurrency but not total volume over time. Repeating with distinct Sierra source variants (e.g., trivial constant/function-name differences) trivially produces new class hashes to defeat the per-class-hash dedup, enabling unbounded disk growth over time from a single low-cost source (or many if rate-limited per IP).

### Recommendation
Defer persistent storage of the compiled class artifact until after stateful validation (nonce/balance/signature) succeeds, or introduce a bounded, TTL-based staging area (with total size cap and per-sender/global quotas) for compiled-but-not-yet-validated classes, evicting entries that are not associated with a transaction that reaches the mempool/gets included within a bounded time window. At minimum, add a global disk-usage cap and eviction policy in `FsClassStorage`/`CachedClassStorage` for classes not yet confirmed declared on-chain.

### Proof of Concept
1. Craft a syntactically valid Sierra contract class close to `max_contract_class_object_size` (4,089,446 bytes) / `max_contract_bytecode_size` (81,920 bytes), varying trivial content to yield a fresh `class_hash` each time.
2. Submit a `Declare` V3 RPC transaction referencing this class, from an arbitrary (unfunded or non-existent) `sender_address`, with an arbitrary signature (only length-bounded, not verified at this stage) and a resource bound that passes `validate_resource_bounds`.
3. Observe: `stateless_tx_validator.validate` passes; the gateway compiles the Sierra to CASM and calls `ClassManager::add_class`, which persists both files to `FsClassStorage` (verifiable by disk usage growth under the class manager's persistent root) — this happens in `add_tx_inner` prior to any nonce/balance check.
4. Observe the subsequent stateful validation call (`extract_state_nonce_and_run_validations`) reject the transaction (e.g., insufficient balance / invalid nonce), yet the class artifact remains permanently on disk with no cleanup.
5. Repeat with new unique Sierra variants (or from many source IPs) to grow disk usage without bound, eventually exhausting the class-manager's disk volume.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L235-236)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L240-266)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-179)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L399-410)
```rust
    async fn run_pre_validation_checks(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<bool> {
        self.validate_state_preconditions(executable_tx, account_nonce).await?;
        validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
        let skip_validate =
            skip_stateful_validations(executable_tx, account_nonce, mempool_client.clone()).await?;
        Ok(skip_validate)
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L27-30)
```rust
// Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during
// transaction ingestion before the transaction's signature and balance are verified. Bound the
// number of compilations running concurrently to protect the node from resource exhaustion.
//
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-203)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L951-970)
```rust
fn delete_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    thin_state_diff: &ThinStateDiff,
    declared_classes_table: &'env DeclaredClassesTable<'env>,
    file_handlers: &FileHandlers<RW>,
) -> StorageResult<IndexMap<ClassHash, SierraContractClass>> {
    let mut deleted_data = IndexMap::new();
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
            continue;
        };
        deleted_data.insert(
            *class_hash,
            file_handlers.get_contract_class_unchecked(contract_class_location)?,
        );
        declared_classes_table.delete(txn, class_hash)?;
    }

    Ok(deleted_data)
}
```
