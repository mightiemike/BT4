### Title
Declared Sierra classes are compiled and persisted to disk before fee/nonce validation, allowing unbounded disk consumption via never-included declare transactions - (File: crates/apollo_class_manager/src/class_manager.rs)

### Summary
The gateway compiles and permanently persists every submitted declare transaction's Sierra class to the class manager's content-addressed filesystem store *before* any stateful validation (nonce/balance/fee) is performed. A sender can submit an unbounded stream of syntactically-valid but ultimately rejected declare transactions (unique Sierra programs → unique class hashes), each of which is compiled and durably written to disk, with no reclamation path when the underlying transaction is later rejected and never included in a block. This is directly analogous to CVE-2016-7498, where OpenStack Nova failed to clean up disk artifacts for instances left in an intermediate "resize" state, causing unbounded disk consumption.

### Finding Description
The gateway's transaction-submission flow shows that for `Declare` transactions, `add_class` (which triggers Sierra→CASM compilation and disk persistence) happens as part of `convert_rpc_tx_to_internal_and_executable_txs`, which runs strictly before `stateful_tx_validator_factory` / `extract_state_nonce_and_run_validations`: [1](#0-0) 

`ClassManager::add_class` computes the class hash, invokes the compiler, and then unconditionally persists the class and its compiled artifact via `self.classes.set_class(...)`, with no linkage to whether the originating transaction will ever be accepted, fee-paid, or included in a block: [2](#0-1) 

The actual disk write is durable and atomic (rename into a content-addressed persistent directory): [3](#0-2) 

Only *after* this compilation/persistence step does the gateway perform stateful validation — nonce checks, balance/fee checks, and the account's `__validate_declare__` call: [4](#0-3) [5](#0-4) 

If stateful validation subsequently rejects the transaction (e.g., `InvalidTransactionNonce`, `ValidateFailure`, insufficient fee/balance), the transaction never reaches the mempool/block, but the already-compiled and persisted Sierra/CASM files for that unique class hash remain on disk indefinitely. I found no eviction, TTL, or garbage-collection path in `apollo_class_manager` that reclaims disk space for classes that were compiled but never successfully declared on-chain (the only cache-size-bounded eviction is the in-memory `CachedClassStorage` LRU layer, not the underlying persistent filesystem store).

The only mitigation present is a semaphore limiting *concurrent* compilations (`declare_compilation_semaphore`), which bounds CPU/memory pressure but does nothing to bound the cumulative number of unique classes persisted to disk over time: [6](#0-5) 

Because class hashes are derived from the Sierra program content, an attacker can trivially generate an unbounded number of distinct-but-invalid (or intentionally-failing) declare transactions from a single account, each producing a unique class hash and therefore a unique, permanently-persisted directory on disk — none of which require the transaction to actually succeed or pay fees, since compilation/storage precedes fee and nonce enforcement.

### Impact Explanation
An attacker sending a stream of declare transactions that are guaranteed or engineered to fail stateful validation (e.g., wrong nonce, insufficient balance, failing `__validate_declare__`) can force the sequencer's class manager to durably persist an unbounded number of unique Sierra/CASM class artifacts to disk, without ever paying the associated declare fee and without the transaction ever being included in a block. Sustained abuse exhausts local disk space on the class-manager/gateway nodes, which can crash or become unable to write new class data, headers, or state — ultimately resulting in the network being unable to confirm new (declare) transactions, matching the "network unable to confirm new transactions" impact criterion.

### Likelihood Explanation
The attack requires only the ability to submit gateway `add_transaction` (declare) requests from a single account — no special privileges, no staker/prover access, and, critically, no requirement to actually possess sufficient balance to pay for the declare (since compilation/storage happens prior to balance/fee validation). The compilation-concurrency semaphore limits burst throughput but not sustained, low-and-slow abuse over time, which is sufficient for the disk-based DoS.

### Recommendation
Defer Sierra→CASM compilation and/or persistent disk write until after stateful validation (nonce, balance, fee, `__validate_declare__`) succeeds, or stage compiled classes in a bounded, time-limited temporary area that is reclaimed if the corresponding declare transaction is rejected or expires without being included in a block. Additionally, consider adding disk-usage quotas / TTL-based garbage collection in `FsClassStorage` for classes that were persisted but never referenced by a committed state diff.

### Proof of Concept
1. From a single account (balance not required to be sufficient, since compilation precedes balance checks), repeatedly submit `Declare` transactions each carrying a distinct, trivially-mutated Sierra program (e.g., differing by an unused constant), producing a unique `class_hash` each time.
2. Ensure each transaction fails stateful validation deterministically (e.g., submit with an intentionally wrong nonce, or an account with insufficient funds/`__validate_declare__` failure) so it never reaches the mempool or gets included in a block.
3. Observe via `apollo_class_manager`'s `FsClassStorage` that each attempt nonetheless creates a new persistent, content-addressed directory (`write_class_atomically` → `rename_to_persistent_dir`) containing the Sierra and CASM files, as demonstrated by `crates/apollo_class_manager/src/class_storage_test.rs:100-123` (`temp_dir_location_and_atomic_write_layout`), which verifies these files are written to a stable persistent directory.
4. Repeating steps 1–3 at scale accumulates unbounded disk usage with no corresponding cleanup mechanism, since no code path in `apollo_class_manager` reclaims storage for classes whose declare transaction was ultimately rejected.

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L245-300)
```rust
    fn validate_nonce(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
    ) -> StatefulTransactionValidatorResult<()> {
        let incoming_tx_nonce = executable_tx.nonce();

        let create_error = |message: String| {
            debug!("{message}");
            StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::InvalidTransactionNonce,
                ),
                message,
            }
        };

        match executable_tx {
            // Declare transactions must have the same nonce as the account nonce.
            ExecutableTransaction::Declare(_) if self.config.reject_future_declare_txs => {
                if incoming_tx_nonce != account_nonce {
                    return Err(create_error(format!(
                        "Invalid transaction nonce. Expected: nonce = {account_nonce}, got: \
                         {incoming_tx_nonce}."
                    )));
                }
            }
            // Deploy account transactions must have nonce 0.
            ExecutableTransaction::DeployAccount(_) => {
                if account_nonce != Nonce(Felt::ZERO) {
                    return Err(create_error(format!(
                        "Invalid deploy account transaction. Account is already deployed \
                         (nonce={account_nonce})."
                    )));
                }
                if incoming_tx_nonce != Nonce(Felt::ZERO) {
                    return Err(create_error(format!(
                        "Invalid transaction nonce. Expected: nonce = 0, got: {incoming_tx_nonce}."
                    )));
                }
            }
            // Other transactions must be within the allowed nonce range.
            _ => {
                let max_allowed_nonce =
                    Nonce(account_nonce.0 + Felt::from(self.config.max_allowed_nonce_gap));
                if !(account_nonce <= incoming_tx_nonce && incoming_tx_nonce <= max_allowed_nonce) {
                    return Err(create_error(format!(
                        "Invalid transaction nonce. Expected: {account_nonce} <= nonce <= \
                         {max_allowed_nonce}, got: {incoming_tx_nonce}."
                    )));
                }
            }
        }

        Ok(())
    }
```
