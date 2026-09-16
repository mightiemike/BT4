This confirms the analog. There is no `remove_class`/`delete_class` function in `apollo_class_manager`, meaning classes stored via `add_class` are never purged. Let me verify the class storage persistence and permit/rate-limiting to complete the picture.### Title
Unpermissioned Declare-transaction rejection permanently persists compiled contract classes, enabling unbounded storage growth - ([File: crates/apollo_transaction_converter/src/transaction_converter.rs])

### Summary
Any unprivileged account can submit a `Declare` transaction whose Sierra class gets compiled and permanently written to the sequencer's persistent class storage (`FsClassStorage`, via `apollo_class_manager`) *before* the transaction is otherwise validated (declarer authorization aside) and even when the transaction is ultimately rejected. Because there is no eviction/removal path for classes once stored, an attacker can submit an unbounded number of distinct, syntactically-valid but semantically-doomed Declare transactions to accumulate disk-resident compiled classes indefinitely, exactly mirroring the OpenBao Kerberos bug class (CWE-770: hidden resource accumulated on the server despite the caller receiving only an error).

### Finding Description
In `TransactionConverter::convert_rpc_tx_to_internal`, when handling a `RpcDeclareTransaction::V3`, the code immediately calls: [1](#0-0) 

`self.class_manager_client.add_class(tx.contract_class).await?` compiles the Sierra class to CASM and stores both the raw Sierra and the compiled executable class via `ClassManager::add_class`: [2](#0-1) 

The class is written to `self.classes.set_class(...)`, which persists it through `CachedClassStorage::set_class` into the underlying `FsClassStorage` (disk-backed storage), *only after* checking `if self.class_cached(class_id) { return Ok(...) }` — there is no equivalent for eviction on failure: [3](#0-2) 

Crucially, this happens *before* the compiled-class-hash consistency check that can reject the transaction: [4](#0-3) 

If `tx.compiled_class_hash != executable_class_hash_v2`, the function returns `Err(TransactionConverterError::ValidateCompiledClassHashError(...))` — but the class was **already persisted** in the previous line. The gateway then surfaces only an error to the caller (`transaction_converter_err_to_deprecated_gw_err`), exactly as OpenBao's Kerberos handler surfaces only an error while a `logical.Auth` object (token) is silently created: [5](#0-4) 

The overall flow (per the project's own sequence diagram) confirms `add_class` occurs unconditionally for any Declare transaction, ahead of stateful validation and mempool admission:



The only gate before `add_class` is the declarer authorization check (`check_declare_permissions`), which is optional/disabled by default (`authorized_declarer_accounts: None` allows any account) and stateless validation (`stateless_tx_validator.validate`), neither of which prevents this from an unprivileged, funded-or-unfunded account, since account funding/nonce/balance is checked only *after* `add_class` runs in `extract_state_nonce_and_run_validations`: [6](#0-5) 

There is no code path anywhere in the class manager (`remove_class`/`delete_class`/`evict` do not exist for this component) that ever removes a persisted class once written, whether or not the Declare transaction that caused it succeeds, is rejected by the mempool, fails stateful validation, or never gets included in a block.

### Impact Explanation
This matches the "storage usage / uncontrolled resource consumption" impact category (CWE-770) of the analog report. An attacker who can reach the gateway's `add_tx` endpoint (any unprivileged transaction sender, no special permissions required when `authorized_declarer_accounts` is not configured) can:
1. Submit a distinct Sierra contract class per Declare tx (varying trivial code so each has a unique class hash).
2. Deliberately mismatch `compiled_class_hash` (or rely on any other downstream rejection, e.g., insufficient balance/nonce failing at stateful validation, or duplicate/rate limited mempool rejection) so the transaction is ultimately rejected and never included on-chain.
3. Because compilation and persistent storage occur unconditionally at the gateway layer regardless of downstream rejection, and no eviction path exists, each attempt permanently grows the class-manager's disk usage and cache, with no cost recovered (fees are never charged since the tx never lands in a block).

This is a resource-exhaustion / disk-fill denial-of-service vector against the sequencer's persistent storage, reachable purely through unprivileged transaction submission — a legitimate Medium-severity availability concern analogous to the CVSS `A:L` component of the referenced advisory.

### Likelihood Explanation
High likelihood of reachability: any account (even an unfunded one, since compilation happens before balance/nonce checks) can submit Declare transactions to a public gateway. Compilation cost is bounded per class by size/resource limits (`max_compiled_contract_class_object_size`, declare-compilation semaphore), but there is no limit on the *number* of distinct classes an attacker can submit over time, nor any mechanism to reclaim storage for classes belonging to rejected/never-included transactions.

### Recommendation
Defer persisting the compiled class in `ClassManager`/`FsClassStorage` until after the transaction has passed compiled-class-hash validation (or all more critical/failure-prone checks), or add a cleanup/GC mechanism that removes classes with no corresponding declared transaction committed to state after some retention window. Alternatively, perform the compiled-class-hash comparison using the return value (`executable_class_hash_v2`) *before* calling `set_class`, so mismatched declares never reach persistent storage — this would require restructuring `ClassManager::add_class` to separate the "compile" and "persist" steps, only persisting once the caller (gateway/transaction_converter) confirms hash equality.

### Proof of Concept
1. Deploy/run a local sequencer with `authorized_declarer_accounts: None` (default — no declare authorization restriction).
2. Craft a valid Sierra `SierraContractClass` (unique/trivial per iteration to generate a fresh class hash), computing its true `executable_class_hash_v2` off-band via the same compiler.
3. Submit an `RpcDeclareTransaction::V3` via the gateway's `add_tx` with `compiled_class_hash` intentionally set to an incorrect value (any felt different from the true compiled hash).
4. Observe: `TransactionConverter::convert_rpc_tx_to_internal` calls `class_manager_client.add_class(...)` (line 350), which compiles and persists the class via `ClassManager::add_class` → `classes.set_class(...)` → `FsClassStorage`, then returns `Err(TransactionConverterError::ValidateCompiledClassHashError(...))` back to the caller as an add-transaction failure.
5. Confirm via the class manager's storage/metrics (e.g., `increment_n_classes`/on-disk file for the computed `class_hash`) that the class persists despite the transaction never being admitted to the mempool.
6. Repeat with N distinct classes to demonstrate unbounded disk growth with zero transactions ever committed to a block.

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

**File:** crates/apollo_class_manager/src/class_storage.rs (L106-120)
```rust
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
```

**File:** crates/apollo_gateway/src/gateway.rs (L228-266)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

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

**File:** crates/apollo_gateway/src/gateway.rs (L435-449)
```rust
    async fn convert_rpc_tx_to_internal_and_executable_txs(
        &self,
        tx: RpcTransaction,
        tx_signature: &TransactionSignature,
    ) -> Result<
        (InternalRpcTransaction, AccountTransaction, Option<(ProofFacts, Proof)>),
        StarknetError,
    > {
        let (internal_tx, verification_handle) =
            self.transaction_converter.convert_rpc_tx_to_internal_rpc_tx(tx).await.map_err(
                |e| {
                    warn!("Failed to convert RPC transaction to internal RPC transaction: {}", e);
                    transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
                },
            )?;
```
