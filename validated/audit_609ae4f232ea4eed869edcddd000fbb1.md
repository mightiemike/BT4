## Title
Unbounded persistent disk growth via declare-transaction classes compiled and stored before transaction validation completes — (File: `crates/apollo_class_manager/src/class_manager.rs`)

### Summary
`ClassManager::add_class` compiles a submitted Sierra class and permanently persists it to disk **before** the enclosing Declare transaction's nonce, balance, or signature are validated. If the transaction is subsequently rejected for any reason (bad nonce, insufficient fee/balance, failed `__validate_declare__`, mempool eviction, duplicate submission with a different unique class, etc.), the compiled class remains in the class manager's persistent filesystem storage forever — there is no deletion/eviction path anywhere in the class-manager code. An unprivileged declare-transaction sender can therefore force unbounded, permanent disk growth on class-manager nodes by repeatedly submitting syntactically valid declares with unique classes that are guaranteed to fail stateful validation, mirroring the CVE's pattern of caching unauthenticated/rejected message content that is never expunged.

### Finding Description
The transaction submission flow is:
1. Gateway performs only stateless (format/signature-shape) validation, then converts the RPC tx to internal form: `TransactionConverter::convert_rpc_tx_to_internal` calls `self.class_manager_client.add_class(tx.contract_class)` for declare transactions, *before* any stateful validation runs. [1](#0-0) 
2. This happens in `Gateway::add_tx_inner` prior to `extract_state_nonce_and_run_validations`, which is where nonce/fee/balance/signature checks actually occur. [2](#0-1) 
3. `ClassManager::add_class` compiles the Sierra class to CASM and, on success, unconditionally calls `self.classes.set_class(...)`, which persists the class to disk via `CachedClassStorage`/`FsClassStorage` — a permanent, unbounded filesystem store (as opposed to the bounded, evictable `GlobalContractCache` LRU layers). [3](#0-2) [4](#0-3) 
4. `FsClassStorage` writes classes into a persistent directory tree keyed by class hash with no size cap, TTL, or cleanup routine. [5](#0-4) 
5. There is no code path anywhere in the class-manager crate that removes, expires, or garbage-collects a stored class once written — a `grep` for removal/eviction logic (`remove_class`, `delete_class`, `evict`) in the class-manager code returns nothing, confirming stored classes are permanent regardless of whether the associated declare transaction is ever included in a block.

The only mitigation present, `declare_compilation_semaphore` (`max_concurrent_declare_compilations`, default 40), bounds *concurrent* compilation load — it protects CPU/memory during compilation but does nothing to bound the cumulative number of classes permanently written to disk over time. [6](#0-5) [7](#0-6) 

This is directly analogous to the referenced CVE: a server (the class manager) accepts and caches attacker-supplied auxiliary data (the compiled class) associated with a message (the declare transaction) *before* the message is fully validated/accepted, and never expunges that data if the message is ultimately rejected — leading to unbounded resource growth from repeated attacker requests (CWE-770).

### Impact Explanation
An attacker who can submit declare transactions (subject to whatever `authorized_declarer_accounts`/`check_declare_permissions` gating applies) can craft an unlimited stream of syntactically distinct Sierra classes (trivial per-class variation changes the class hash, bypassing the "already cached" short-circuit) attached to declare transactions engineered to fail stateful validation (e.g., insufficient balance, bad nonce, or a class whose declare will never be included). Each such submission causes:
- A real Sierra→CASM compilation (CPU cost, rate-limited by the semaphore).
- A permanent write to the class manager's on-disk persistent storage, regardless of the transaction's ultimate rejection.

Repeated over time this exhausts class-manager disk space, eventually causing storage-write failures or node crashes. Because the class manager is a required component of transaction ingestion, exhausting its disk affects a sequencer node's ability to admit new declare transactions and can degrade or halt block production, satisfying the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
The class-compile-then-store step is unconditionally reached by any declare transaction that passes stateless validation, well before stateful (fee/nonce/signature) checks run, so the ordering issue is deterministic and trivially reachable by a single unprivileged transaction sender (or anyone permitted to send declare transactions under the deployment's `authorized_declarer_accounts`/`block_declare` policy). No node operator or protocol-level condition is required beyond the ability to submit transactions.

### Recommendation
- Defer class compilation/persistent storage until after stateful validation (nonce, fee/balance, signature) succeeds, or store compiled classes only in a bounded, evictable cache until the declare transaction is confirmed in the mempool/block.
- Alternatively, add an explicit cleanup/expiration mechanism for classes stored in `FsClassStorage` that are never referenced by a committed state diff within some time bound, and/or bound total persistent class storage size with an eviction policy (LRU or size cap), consistent with the bounded caches used elsewhere (`GlobalContractCache`, mempool `capacity_in_bytes`, `TimeCache`).
- Consider deduplicating on a hash of the raw Sierra bytes across unrelated senders/nonces before committing to disk, and charging/limiting compilation+storage cost per sender independent of the process-wide concurrency semaphore.

### Proof of Concept
1. Submit a declare transaction with a valid Sierra class (compiles successfully) but with a nonce/fee/signature guaranteed to fail stateful validation (e.g., wrong nonce or account with insufficient balance).
2. Observe: `add_tx_inner` calls `convert_rpc_tx_to_internal_and_executable_txs` → `ClassManager::add_class` → `CachedClassStorage::set_class`, which persists the compiled class to `FsClassStorage`'s `persistent_root` on disk, before `extract_state_nonce_and_run_validations` runs and rejects the transaction.
3. Confirm via the class manager's storage directory that the class files remain on disk after the transaction is rejected/dropped by the gateway/mempool.
4. Repeat with N distinct classes (varying trivial constants to get unique class hashes) to observe linear, unbounded growth in class-manager disk usage with no corresponding cleanup.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-350)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L240-251)
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
```

**File:** crates/apollo_gateway/src/gateway.rs (L253-266)
```rust
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

**File:** crates/apollo_class_manager/src/class_storage.rs (L99-138)
```rust
impl<S> ClassStorage for CachedClassStorage<S>
where
    S: ClassStorage,
    CachedClassStorageError<S::Error>: From<S::Error>,
{
    type Error = CachedClassStorageError<S::Error>;

    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.class_cached(class_id) {
            return Ok(());
        }

        self.storage.set_class(
            class_id,
            class.clone(),
            executable_class_hash_v2,
            executable_class.clone(),
        )?;

        increment_n_classes(CairoClassType::Regular);
        record_class_size(ClassObjectType::Sierra, &class);
        record_class_size(ClassObjectType::Casm, &executable_class);

        // Cache the class.
        // Done after successfully writing to storage as an optimization;
        // does not require atomicity.
        self.classes.set(class_id, class);
        self.executable_classes.set(class_id, executable_class);
        // Cache the executable class hash last; acts as an existence marker.
        self.executable_class_hashes_v2.set(class_id, executable_class_hash_v2);

        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L351-404)
```rust
impl FsClassStorage {
    pub fn new(
        config: FsClassStorageConfig,
        storage_reader_server_dynamic_config: StorageReaderServerDynamicConfig,
        dynamic_config_provider: SharedDynamicConfigProvider,
    ) -> FsClassStorageResult<Self> {
        let storage_reader_server_config = ServerConfig {
            static_config: config.storage_reader_server_static_config.clone(),
            dynamic_config: storage_reader_server_dynamic_config,
        };
        let class_hash_storage = ClassHashStorage::new(
            config.class_hash_storage_config,
            storage_reader_server_config,
            dynamic_config_provider,
        )?;
        std::fs::create_dir_all(&config.persistent_root)?;
        Ok(Self { persistent_root: config.persistent_root, class_hash_storage })
    }

    fn contains_class(&self, class_id: ClassId) -> FsClassStorageResult<bool> {
        Ok(self.get_executable_class_hash_v2(class_id)?.is_some())
    }

    // TODO(Elin): make this more robust; checking file existence is not enough, since by reading
    // it can be deleted.
    fn contains_deprecated_class(&self, class_id: ClassId) -> bool {
        self.get_deprecated_executable_path(class_id).exists()
    }

    /// Returns the directory that will hold classes related to the given class ID.
    /// For a class ID: 0xa1b2c3d4... (rest of hash), the structure is:
    /// a1/
    /// └── b2/
    ///     └── a1b2c3d4.../
    fn get_class_dir(&self, class_id: ClassId) -> PathBuf {
        let class_id = hex::encode(class_id.to_bytes_be());
        let (first_msb_byte, second_msb_byte, _rest_of_bytes) =
            (&class_id[..2], &class_id[2..4], &class_id[4..]);

        PathBuf::from(first_msb_byte).join(second_msb_byte).join(class_id)
    }

    fn get_persistent_dir(&self, class_id: ClassId) -> PathBuf {
        self.persistent_root.join(self.get_class_dir(class_id))
    }

    fn get_persistent_dir_with_create(&self, class_id: ClassId) -> FsClassStorageResult<PathBuf> {
        let path = self.get_persistent_dir(class_id);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        Ok(path)
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L27-38)
```rust
// Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during
// transaction ingestion before the transaction's signature and balance are verified. Bound the
// number of compilations running concurrently to protect the node from resource exhaustion.
//
// Derivation: compilations are served by the sierracompiler instances, so the safe per-gateway
// bound is the sierracompiler fleet's headroom divided across the gateway fleet, i.e.
// `per_instance_capacity * num_sierracompiler_instances / num_gateway_instances`. Observed
// sierracompiler usage per compilation is small (memory spike ~0.75% of an instance), so a single
// instance can absorb many concurrent compilations. 40 stays well within that envelope while still
// capping the blast radius of a declare flood; retune via the formula above if the
// sierracompiler/gateway instance ratio or per-instance capacity changes.
const DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS: usize = 40;
```
