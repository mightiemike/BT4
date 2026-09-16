This confirms the vulnerability chain. `TransactionConverter::convert_rpc_tx_to_internal` calls `self.class_manager_client.add_class(tx.contract_class)` for every `Declare` transaction [1](#0-0) , and `ClassManager::add_class` compiles the Sierra to CASM and then **permanently persists both to disk** via `self.classes.set_class(...)` [2](#0-1) , which ultimately calls `FsClassStorage::write_class_atomically`/`rename_to_persistent_dir` to write the class files into the persistent, content-addressed class directory [3](#0-2) . This disk write happens in `Gateway::add_tx_inner` at `convert_rpc_tx_to_internal_and_executable_txs`, which is invoked **before** `stateful_tx_validator_factory.instantiate_validator(...)` / `extract_state_nonce_and_run_validations` — i.e., before nonce, balance, fee, or `__validate__` entry-point checks [4](#0-3) .

### Title
Unbounded permanent disk-space consumption via unpaid, unvalidated Declare transactions before stateful validation - (File: crates/apollo_transaction_converter/src/transaction_converter.rs)

### Summary
Any unprivileged network caller can submit `Declare` (RPC v3) transactions to the gateway's `add_tx` endpoint. Class compilation and **permanent** persistence to the class manager's filesystem store occur unconditionally during RPC→internal transaction conversion, before the gateway performs stateful validation (nonce check, balance/fee check, `__validate__` execution). Because storage is content-addressed by class hash, an attacker can craft an unlimited stream of distinct Sierra programs (unique but only trivially so, e.g. differing by an embedded constant), each sized up to the configured limits, to force the sequencer to compile and durably write class artifacts to disk without ever paying gas, without a valid account/signature passing stateful checks, and even if the underlying declare transaction is ultimately rejected by the mempool or never included in a block.

### Finding Description
The transaction submission flow is: stateless validation → (for Declare) `class_manager_client.add_class` → stateful validation → mempool admission [5](#0-4) .

`Gateway::add_tx_inner` calls `convert_rpc_tx_to_internal_and_executable_txs` prior to instantiating and running the `StatefulTransactionValidator` (nonce/balance/`__validate__`) [4](#0-3) . That conversion routine, for `Declare` transactions, unconditionally calls `self.class_manager_client.add_class(tx.contract_class)` [1](#0-0) .

`ClassManager::add_class` deduplicates only identical class hashes (`if let Ok(Some(...)) = self.classes.get_executable_class_hash_v2(...) { return Ok(...) }`), then compiles Sierra→CASM and calls `self.classes.set_class(...)`, which durably persists the Sierra program and compiled CASM to the filesystem class store [6](#0-5) . `CachedClassStorage::set_class` forwards to the underlying `FsClassStorage`, which atomically renames a temp directory into the content-addressed persistent directory under `persistent_root` (default `/data/classes`) [3](#0-2) . There is no eviction of these persistent files, no per-account or per-time quota on the number of distinct classes an address may declare, and no requirement that the declaring transaction ever pass fee/balance/signature checks or be included in a block.

The only mitigation present, `declare_compilation_semaphore` / `max_concurrent_declare_compilations` (default 40), limits **concurrent** CPU-bound compilations, not the cumulative **disk** growth over time from many sequential/serialized submissions [7](#0-6) [8](#0-7) . Per-transaction size caps exist (`max_contract_bytecode_size` = 81920 felts, `max_contract_class_object_size`/`max_compiled_contract_class_object_size` ≈ 4 MB) [9](#0-8) [10](#0-9) , but these bound the size of a single class, not the total number of unique classes an attacker can force onto disk over time — nothing rejects the tx or reclaims disk before the class is persisted if the stateful validation subsequently fails (bad signature, insufficient balance, bad nonce, etc.). This directly mirrors the Synapse CVE-2024-37302 pattern: an unauthenticated/unprivileged actor can induce the server to permanently cache attacker-supplied, sizable data with insufficient rate limiting, leading to disk exhaustion.

### Impact Explanation
Sustained submission of syntactically valid but semantically worthless (or malformed-signature) Declare transactions, each carrying a near-max-size but unique Sierra program, causes the class manager to compile and permanently write to its persistent filesystem store for every submission — independent of whether the sender holds funds, has a valid signature, or the transaction ever reaches the mempool/block. Once disk fills (`/data/classes` in the default deployment), the class manager and any co-located sequencer components can no longer write, degrading or halting new transaction admission network-wide — a network-availability (DoS) impact ("a network unable to confirm new transactions").

### Likelihood Explanation
Reachable directly by any unauthenticated user through the public RPC `add_transaction` endpoint, requiring only crafting distinct Sierra programs within existing size limits (no privileged access, no valid funded account, no successful stateful validation needed). The only throttle is a concurrency semaphore, which does not bound sustained sequential throughput or aggregate disk growth over time, so the attack is straightforward to sustain at low cost.

### Recommendation
Defer persistent class storage writes until after stateful validation (nonce, balance/fee, `__validate__`) succeeds, or stage class artifacts in a bounded, time-limited/temporary cache during validation and only commit to permanent storage once the transaction is admitted to the mempool. Additionally, add a genuine rate/quota limit on Sierra-to-CASM compilation and persistent-class writes (e.g., per-sender or per-IP declare-and-persist rate limiting analogous to Synapse's "leaky bucket"), separate from the existing pure-concurrency semaphore, and consider requiring proof of fee sufficiency (e.g., a lightweight balance check) before compiling/persisting the class.

### Proof of Concept
1. Prepare N syntactically valid `Declare` V3 RPC transactions, each with a distinct `SierraContractClass.sierra_program` (e.g., append a unique felt literal near the `max_contract_bytecode_size` boundary), each under `max_contract_class_object_size`, with arbitrary/garbage `signature` and `sender_address` that will fail `__validate__` or nonce checks.
2. Submit them sequentially (or up to `max_concurrent_declare_compilations` in parallel, then repeat) to the gateway's `add_tx` (`starknet_addDeclareTransaction`).
3. Observe `Gateway::add_tx_inner` invoking `convert_rpc_tx_to_internal_and_executable_txs` → `TransactionConverter::convert_rpc_tx_to_internal` → `ClassManager::add_class`, which persists each unique class's Sierra+CASM to `FsClassStorage`'s persistent directory, before the subsequent `extract_state_nonce_and_run_validations` call rejects the transaction (e.g., `InvalidTransactionNonce`/`ValidateFailure`).
4. Repeat at the rate of `max_concurrent_declare_compilations` (default 40) sustained submissions; disk usage under `persistent_root` (default `/data/classes`) grows unboundedly with every distinct rejected/never-included Declare, even though none of the transactions are ever paid for or committed to a block.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-350)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
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

**File:** crates/apollo_class_manager/src/class_storage.rs (L451-500)
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

**File:** docs/diagrams/02-tx-submission-flow.md (L23-41)
```markdown
    User->>HTTP: POST /add_transaction
    HTTP->>GW: add_tx(GatewayInput)

    Note over GW: Stateless validation<br/>(format, signature)

    alt Declare Transaction
        GW->>CM: add_class(SierraContractClass)
        CM->>Compiler: compile(RawClass)
        Compiler-->>CM: RawExecutableClass
        CM-->>GW: Sierra & CASM Hashes
    end

    rect rgb(240, 248, 255)
        Note over GW,SS: Stateful validation (via Blockifier)
        GW->>SS: get_nonce_at(block_number, contract_address)
        SS-->>GW: Nonce
        GW->>SS: read state (balance, storage, etc.)
        SS-->>GW: state data
    end
```

**File:** crates/apollo_gateway_config/src/config.rs (L30-38)
```rust
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L315-337)
```rust
    fn validate_class_length(
        &self,
        contract_class: &starknet_api::state::SierraContractClass,
    ) -> StatelessTransactionValidatorResult<()> {
        if contract_class.sierra_program.len() > self.config.max_contract_bytecode_size {
            return Err(StatelessTransactionValidatorError::ContractBytecodeSizeTooLarge {
                contract_bytecode_size: contract_class.sierra_program.len(),
                max_contract_bytecode_size: self.config.max_contract_bytecode_size,
            });
        }

        let contract_class_object_size = serde_json::to_string(&contract_class)
            .expect("Unexpected error serializing contract class.")
            .len();
        if contract_class_object_size > self.config.max_contract_class_object_size {
            return Err(StatelessTransactionValidatorError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self.config.max_contract_class_object_size,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_class_manager_config/src/config.rs (L16-16)
```rust
const DEFAULT_MAX_COMPILED_CONTRACT_CLASS_OBJECT_SIZE: usize = 4089446;
```
