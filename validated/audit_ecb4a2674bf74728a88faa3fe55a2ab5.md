This confirms the analog: `TransactionConverter::convert_rpc_tx_to_internal` calls `class_manager_client.add_class(tx.contract_class)` for every `Declare` transaction, which persistently writes the Sierra and compiled CASM class to disk via `FsClassStorage::set_class` → `write_class_atomically` (content-addressed files under `persistent_root`) **before** any fee/nonce/balance stateful validation occurs in `add_tx_inner`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Title
Declare transactions persist Sierra/CASM classes to disk before fee/balance/nonce validation, enabling unbounded disk exhaustion - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
The reported CVE (Fake Stake) describes a DoS where an attacker with minimal resources can force a victim node to persist invalid headers/blocks to disk before those blocks are properly validated, exhausting disk space. The reachable analog in this sequencer is the Declare transaction path: any unprivileged transaction sender can cause the gateway to compile and durably write a new Sierra class and its compiled CASM to the class-manager's filesystem storage *before* the transaction undergoes stateful validation (balance/fee/nonce checks), and even before the compiled-class-hash consistency check against the sender-supplied `compiled_class_hash` is verified.

### Finding Description
In `Gateway::add_tx_inner`, the order of operations is:
1. Stateless validation (`stateless_tx_validator.validate`).
2. `convert_rpc_tx_to_internal_and_executable_txs`, which for `Declare` transactions calls `class_manager_client.add_class(tx.contract_class)`.
3. Only afterwards is stateful validation performed (`extract_state_nonce_and_run_validations`), which checks account nonce, balance/fee, and runs `__validate_declare__`. [1](#0-0) 

`class_manager_client.add_class` → `ClassManager::add_class` compiles the Sierra class via the Sierra-to-CASM compiler and, if the class hash is not already known, calls `self.classes.set_class(...)`, which persists the class to the `CachedClassStorage`/`FsClassStorage` backend. [3](#0-2) 

`FsClassStorage::set_class` performs an atomic, durable filesystem write (`write_class_atomically` → `std::fs::rename` into a persistent, content-addressed directory keyed by class hash) and records an existence marker in a persistent DB (`ClassHashStorage`). [6](#0-5) 

The only checks performed before this disk write are: stateless validation (format checks, resource-bound sanity, size limits), and a semaphore limiting *concurrent* compilations (`declare_compilation_semaphore`, default 40) — there is no check that the sender has funds to pay for the declare, that the sender's nonce is valid, or that the transaction will pass `__validate_declare__`. The `compiled_class_hash` mismatch check happens only *after* the class is already written to disk (the check is applied to the returned `ClassHashes`, but the write already occurred inside `add_class`). [7](#0-6) 

Because class storage is content-addressed by `class_hash`, an attacker who submits many *distinct* Declare transactions with unique (even trivial/near-maximum-size) Sierra contract classes — each doomed to fail stateful validation due to insufficient balance or an invalid nonce — still causes each unique class to be durably written to disk (bounded per-class by `max_compiled_contract_class_object_size`, but with no bound tied to whether the declaring account can actually afford/execute the declare). Once written, `FsClassStorage::set_class` for the same `class_hash` becomes a no-op (dedup by content hash), so the disk write is not repeated per resubmission of the *same* class, but each distinct declared Sierra program constitutes a new persistent entry regardless of transaction validity. Rejected transactions are never subsequently cleaned up from class storage.

### Impact Explanation
This allows an unprivileged transaction sender to make the sequencer/gateway (and its class-manager component with `FsClassStorage`) permanently persist attacker-supplied, otherwise-invalid Declare payloads to disk, growing storage without the transaction ever being economically validated or included in the mempool/chain. Repeated in volume (bounded only by rate limits, the concurrent-compilation semaphore, and per-class size caps — not by economic cost of a successful declare), this can exhaust disk space on gateway/class-manager nodes, a resource-exhaustion condition directly analogous to the "Fake Stake" disk-filling attack in the CVE.

### Likelihood Explanation
Reachable by any single unprivileged transaction sender through the normal `add_tx` RPC path (or via P2P propagation) with no special privileges, stake, or prior state — only the ability to submit a syntactically valid Declare transaction with a unique Sierra class body and pass cheap stateless checks (size ≤ `max_compiled_contract_class_object_size`, resource bounds format). The declare-compilation semaphore limits *concurrency* but not the aggregate rate/volume of sequential submissions over time, and none of the pre-write checks require the sender to actually be able to pay for or successfully validate the declare.

### Recommendation
Reorder the Declare transaction pipeline so that stateful validation (nonce and fee/balance checks, and ideally the `compiled_class_hash` equality check) is fully performed *before* `class_manager_client.add_class` is invoked and its result persisted to `FsClassStorage`, or gate persistence on an economic/nonce precondition (e.g., compile/validate the class hash in a way that avoids durable writes for transactions that cannot pass basic account-state checks). Additionally consider adding a per-account or global rate limit on declare-driven disk writes independent of the existing concurrency semaphore, and a cleanup/garbage-collection mechanism for classes that were persisted but whose originating declare transaction was ultimately rejected.

### Proof of Concept
1. Create N accounts (or reuse one account with distinct nonces is not required — class storage is not bound to sender) with insufficient balance/invalid nonce so `extract_state_nonce_and_run_validations` will always fail.
2. For each of N submissions, craft a syntactically valid `RpcDeclareTransaction::V3` with a unique Sierra `contract_class` body (near `max_compiled_contract_class_object_size`) and any `compiled_class_hash`/insufficient fee.
3. Submit each via the gateway `add_tx` endpoint.
4. Observe: `Gateway::add_tx_inner` → `convert_rpc_tx_to_internal_and_executable_txs` → `ClassManager::add_class` compiles and calls `self.classes.set_class(...)`, causing `FsClassStorage::write_class_atomically` to persist the Sierra+CASM files to `persistent_root` on disk, even though the subsequent `extract_state_nonce_and_run_validations` call fails and the transaction is ultimately rejected (`add_tx_inner` returns an error after the disk write already happened).
5. Repeating this with many unique classes grows disk usage on the class-manager storage without bound tied to successful, fee-paying declarations.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L235-266)
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-360)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
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

**File:** crates/apollo_class_manager/src/class_storage.rs (L451-522)
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

    fn write_deprecated_class_atomically(
        &self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> FsClassStorageResult<()> {
        // Write class to a temporary directory.
        let (_tmp_root, tmp_dir) = self.create_tmp_dir(class_id)?;
        class.write_to_file(concat_deprecated_executable_filename(&tmp_dir))?;

        self.rename_to_persistent_dir(tmp_dir, class_id)
    }

    /// Atomically moves the staged class directory `tmp_dir` into its content-addressed persistent
    /// directory.
    ///
    /// Recovers from a previous partial write: a crash between this rename and committing the
    /// existence marker (see `FsClassStorage::set_class`) can leave an orphaned, non-empty
    /// persistent directory. `std::fs::rename` refuses to replace a non-empty directory and fails
    /// with `ENOTEMPTY`, which permanently wedges sync on the class. Callers reach this only when
    /// the existence marker is absent, and the directory is named by the class hash, so an existing
    /// directory holds the same class; removing it lets the rename proceed and the marker get
    /// written, restoring filesystem/marker consistency.
    fn rename_to_persistent_dir(
        &self,
        tmp_dir: PathBuf,
        class_id: ClassId,
    ) -> FsClassStorageResult<()> {
        let persistent_dir = self.get_persistent_dir_with_create(class_id)?;
        if persistent_dir.exists() {
            warn!("Recovering orphaned class dir from a prior partial write: {persistent_dir:?}");
            std::fs::remove_dir_all(&persistent_dir)?;
        }
        std::fs::rename(tmp_dir, persistent_dir)?;

        Ok(())
    }
}

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
