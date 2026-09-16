### Title
Unbounded free disk exhaustion via persisted class writes before fee/nonce validation on DECLARE transactions - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
Every submitted `DECLARE` transaction causes the gateway to compile the supplied Sierra program to CASM and persist both artifacts to the class manager's content-addressed filesystem storage **before** any nonce check, fee validation, or mempool admission occurs. Since compilation/persistence happens unconditionally and there is no cleanup path for classes whose declare transaction is later rejected, an unprivileged sender can submit an unbounded stream of syntactically valid but ultimately-rejected DECLARE transactions (e.g. with a deliberately wrong `compiled_class_hash`, insufficient balance, or bad nonce) to permanently fill sequencer disk with orphaned class artifacts, at effectively zero cost. This mirrors CVE-2013-6437, where Nova created new ephemeral backing files per unique parameter without charging/cleaning up on failure.

### Finding Description
The transaction submission flow calls `ClassManager::add_class` as part of converting an incoming RPC declare transaction, prior to stateful (nonce/fee) validation: [1](#0-0) 

This happens inside `convert_rpc_tx_to_internal`, which is invoked from the gateway's `convert_rpc_tx_to_internal_and_executable_txs` — itself called *before* `stateful_tx_validator_factory...extract_state_nonce_and_run_validations`, i.e. before nonce and fee checks: [2](#0-1) 

`ClassManager::add_class` compiles the Sierra program (CPU cost) and, if the class is new, unconditionally writes both the raw Sierra and compiled CASM to persistent, content-addressed filesystem storage via `CachedClassStorage::set_class` → `FsClassStorage::write_class_atomically`: [3](#0-2) [4](#0-3) 

Because the class hash is derived from the Sierra content itself, an attacker can trivially generate distinct valid Sierra programs (e.g. varying dummy data/entry points) to produce a fresh, unique `class_hash` for every submission, each triggering a new persistent write (up to `max_contract_class_object_size`, configured at ~4 MB) that is never deduplicated against previously rejected declares.

Critically, the code path that would detect a bad declare — e.g. a mismatched `compiled_class_hash` — runs *after* `add_class` has already persisted the class: [1](#0-0) 

No `remove_class`/`delete_class`/eviction mechanism exists in the class manager or class storage to reclaim disk space for classes belonging to transactions that are subsequently rejected by stateful validation, never admitted to the mempool, or never included in a block (confirmed by absence of any such API in `apollo_class_manager`).

The only mitigating control is `declare_compilation_semaphore`, which bounds *concurrent* compilations (default 40) but does not limit the *total* number of unique classes an attacker can persist over time: [5](#0-4) [6](#0-5) 

### Impact Explanation
This is a concrete disk-exhaustion denial-of-service on the sequencer's class manager storage volume, reachable by any unprivileged transaction sender without needing to hold a funded account, a valid nonce, or ever succeeding in getting a declare transaction accepted. Filling the persistent volume can degrade or halt block production and state-sync/storage components across the node (and any component sharing the same FS_class storage volume), i.e. a network unable to confirm new transactions once disk is exhausted.

### Likelihood Explanation
High-likelihood: the attack requires only crafting distinct, size-bounded, syntactically valid Sierra programs (Cairo compiler-buildable) and submitting DECLARE transactions repeatedly; no funds, valid nonce, signature validity against a real account state, or eventual tx success are required, since the vulnerable write occurs prior to those checks.

### Recommendation
Defer Sierra compilation and persistent class storage writes until after nonce and fee/balance pre-validation succeeds (or at minimum, gate persistence on a successful `compiled_class_hash` match and passing stateful pre-validation). Additionally, add a garbage-collection/eviction mechanism in `apollo_class_manager` for classes whose associated declare transaction never reaches inclusion within a bounded time window, and consider charging/reserving fee before compiling+persisting untrusted Sierra content.

### Proof of Concept
1. Generate N syntactically valid Sierra programs, each differing enough (e.g. via unique dummy entry points/felt constants) to produce a unique `class_hash`, sized near `max_contract_class_object_size` (~4 MB).
2. For each, submit an RPC `DECLARE` V3 transaction with an intentionally incorrect `compiled_class_hash` (or from an account with zero balance/invalid nonce).
3. Observe: `add_tx` triggers `TransactionConverter::convert_rpc_tx_to_internal` → `ClassManager::add_class`, which compiles and persists the class to `FsClassStorage` (`crates/apollo_class_manager/src/class_storage.rs`), before the compiled-class-hash mismatch (or nonce/fee) causes the request to be rejected.
4. Repeat with new unique classes; each rejected submission still leaves persisted Sierra+CASM files on disk with no cleanup, growing storage usage unboundedly per attacker request, bounded only by attacker throughput and the 40-way concurrency semaphore (not by total volume).

### Citations

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

**File:** crates/apollo_gateway/src/gateway.rs (L240-267)
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

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-109)
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
