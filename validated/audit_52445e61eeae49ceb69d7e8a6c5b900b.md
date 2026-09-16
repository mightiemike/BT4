## Analysis Result

### Title
Unbounded permanent disk persistence of unpaid, ultimately-rejected declared classes via compiled-class-hash mismatch bypass - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
The JFinal advisory describes a pattern where a file is written to persistent storage first, "safety" validation runs afterward, and the deletion/cleanup step that should remove unsafe/rejected content is skipped in certain code paths, leaving attacker-controlled content permanently on disk. The sequencer's declare-transaction path has the analogous ordering bug: a submitted class is compiled and **permanently persisted to the class manager's filesystem storage before** the transaction's `compiled_class_hash` is checked against the actual compiled hash, and there is no deletion/cleanup path for classes that fail this check.

### Finding Description
In `TransactionConverter::convert_rpc_tx_to_internal`, for a `Declare` transaction, the class is sent to the class manager and stored **before** validating that the sender-supplied `compiled_class_hash` matches the computed one: [1](#0-0) 

`ClassManager::add_class` performs compilation and its own internal checks (size, Sierra version), then unconditionally calls `self.classes.set_class(...)`, which atomically writes the Sierra and CASM files into a content-addressed persistent directory on disk via `FsClassStorage::write_class_atomically` / `rename_to_persistent_dir`: [2](#0-1) [3](#0-2) 

Only *after* this permanent write returns does `convert_rpc_tx_to_internal` compare `tx.compiled_class_hash` against the value computed during compilation, and reject the transaction if they don't match: [4](#0-3) 

Crucially, `FsClassStorage`/`ClassStorage` expose no delete operation — there is no code path anywhere in `class_storage.rs` that removes a previously written class. This mirrors the JFinal bug class exactly: content is durably persisted first, "is this safe/valid" validation happens afterward, and the intended cleanup for the invalid case simply does not exist.

This is reachable from the gateway's `add_tx` flow: stateless validation only checks Sierra version, class size and entry-point ordering — it never checks the declared `compiled_class_hash` — so a class is always compiled and stored before the mismatch check runs: [5](#0-4) [6](#0-5) 

Because rejection due to `CompiledClassHashMismatch` happens inside `convert_rpc_tx_to_internal_and_executable_txs`, which runs *before* `extract_state_nonce_and_run_validations` (nonce/fee/balance checks) and before the transaction ever reaches the mempool, the sender pays no fee and the transaction can be immediately resubmitted: [7](#0-6) 

### Impact Explanation
Any account that passes `check_declare_permissions` (which is a no-op unless a declarer allow-list is explicitly configured) can submit an unlimited stream of syntactically distinct Sierra classes with a deliberately wrong `compiled_class_hash`. Each submission is compiled by the Sierra→CASM compiler and permanently written to the class-manager's persistent filesystem storage, consuming disk space and compiler CPU/memory, while the transaction is guaranteed to fail before fee charging or mempool admission — so the attacker pays nothing and there is no state change to revert. Repeating this indefinitely leads to unbounded growth of on-disk class storage, exhausting the sequencer's disk, which can crash or degrade the node/class-manager component and ultimately prevent the network from confirming new transactions (a DoS on the class-manager/sequencer storage layer) — the exact "content persisted, validation bypassed, cleanup omitted" pattern from the referenced CVE, but at the sequencer's own storage subsystem rather than a served file.

### Likelihood Explanation
High. This requires no special privilege beyond being able to submit a signed declare transaction (the class hash check being missing from `check_declare_permissions`/`stateless_tx_validator.validate` means any standard account can trigger it, assuming declares aren't behind an allow-list). Generating many syntactically valid but distinct Sierra programs (each with a unique class hash to defeat the `contains_class` dedup check) with a wrong `compiled_class_hash` is trivial and does not require any special access, waiting period, or race condition — it is a deterministic, repeatable code path on every declare submission.

### Recommendation
Reorder the declare-transaction handling so the `compiled_class_hash` (and any other stateless/tx-supplied class-hash validations) are checked **before** the class is durably persisted by the class manager, or perform the persistence only after the full compiled-class-hash equality check succeeds. Alternatively, add an explicit rollback/delete path in `ClassStorage`/`FsClassStorage` invoked when the declare transaction is subsequently rejected for `CompiledClassHashMismatch`, so no unpaid, rejected class content survives on disk.

### Proof of Concept
1. Craft a valid Sierra contract class `C` that compiles successfully (passes `validate_sierra_version`, `validate_class_length`, `validate_entry_points_sorted_and_unique`).
2. Set `compiled_class_hash` in the `DECLARE` transaction to an arbitrary incorrect felt (not matching the real CASM hash of `C`).
3. Submit via `starknet_addDeclareTransaction` / `Gateway::add_tx`.
4. Observe: `stateless_tx_validator.validate` passes → `transaction_converter.convert_rpc_tx_to_internal` calls `class_manager_client.add_class(C)`, which compiles `C` and calls `FsClassStorage::set_class`, permanently writing the Sierra/CASM files to `persistent_root/<hash_prefix>/.../<class_hash>/` — then the subsequent `compiled_class_hash` check fails and the transaction is rejected with `CompiledClassHashMismatch`, before any fee is charged or nonce/mempool logic runs.
5. Repeat with new distinct Sierra classes (varying, e.g., an unused function name/selector to get a fresh `class_hash`) to accumulate unbounded persisted classes on disk at zero cost.

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

**File:** crates/apollo_class_manager/src/class_storage.rs (L487-500)
```rust
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
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L280-291)
```rust
    fn validate_declare_tx(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let contract_class = match declare_tx {
            RpcDeclareTransaction::V3(tx) => &tx.contract_class,
        };
        self.validate_sierra_version(&contract_class.sierra_program)?;
        self.validate_class_length(contract_class)?;
        self.validate_entry_points_sorted_and_unique(contract_class)?;
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L236-266)
```rust
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
