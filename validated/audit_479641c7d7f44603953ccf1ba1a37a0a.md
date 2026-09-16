Based on my investigation, I found a genuine analog: the Class Manager's `add_class` compiles and permanently stores a submitted Sierra class **before** any signature, nonce, fee, or balance validation occurs — and the resulting class becomes retrievable via `get_sierra`/`get_executable` with no check that the class was ever actually declared in committed state.

### Title
Declare-transaction class storage/leakage before authorization — unmetered permanent storage and unauthenticated retrieval of undeclared classes - ([File: crates/apollo_class_manager/src/class_manager.rs])

### Summary
`GenericGateway::add_tx_inner` compiles and stores a submitted Sierra class into the class manager's persistent storage during transaction ingestion, prior to the stateful validation stage (signature verification, nonce check, balance/fee check) that determines whether the Declare transaction is actually admissible.

### Finding Description
In `add_tx_inner`, for `RpcTransaction::Declare`, only stateless validation (`self.stateless_tx_validator.validate(&tx)`) runs before the class conversion step, which triggers `ClassManager::add_class` (via `CM->>Compiler: compile` per the flow documented in `docs/diagrams/02-tx-submission-flow.md`). This is confirmed by the config comment in `crates/apollo_gateway_config/src/config.rs:27-29`: "Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during transaction ingestion before the transaction's signature and balance are verified." [1](#0-0) [2](#0-1) 

`ClassManager::add_class` computes the class hash from the submitted content and unconditionally persists both the Sierra and compiled CASM to `FsClassStorage` if not already cached — with no linkage to whether the originating Declare transaction ever passes signature/fee/nonce validation, is admitted to the mempool, or is ever included in a block: [3](#0-2) 

Retrieval via `ClassManager::get_sierra`/`get_executable`, and the underlying `FsClassStorage`/`CachedClassStorage` implementations, perform **no check** that the class is actually declared in committed chain state — they only check existence in local storage: [4](#0-3) [5](#0-4) 

Any component (or a remote client, since `ClassManagerRequest::GetSierra`/`GetExecutable` are served over `RemoteComponentServer`) that can reach the class manager can fetch this content by class hash, regardless of whether the class was ever officially declared. This is directly analogous to the reported Liferay bug class (CWE-552): content is persisted to a storage location keyed by an identifier upon "upload" (Declare submission) and later served on request without verifying that the corresponding record was legitimately/finally authorized (a successfully validated, fee-paid, block-included Declare transaction). [6](#0-5) 

Separately, this also means an attacker can force compilation and permanent disk storage of arbitrary large Sierra programs on every sequencer node in the network by submitting Declare transactions that are guaranteed to fail stateful validation (e.g., insufficient balance/invalid signature), since the class is written to storage *before* that validation occurs, and there is no eviction of classes belonging to rejected transactions.

### Impact Explanation
This allows unauthenticated/unauthorized data persistence and disclosure: a class that was never legitimately declared (never paid for, never included in state) is nonetheless durably stored and can be fetched by class hash from any consumer of `ClassManagerClient`. This is a resource/availability and integrity-adjacent issue (undeclared code being materially indistinguishable in storage from declared code) rather than a fund-loss bug on its own, since Starknet contract classes are intended to eventually be public once declared — the primary damage is (a) unauthorized/unbounded persistent storage growth per rejected Declare (bypassing the fee mechanism meant to price this exact resource) and (b) retrieval of a class's bytecode without any confirmation it was ever declared, which could be leveraged to probe/exfiltrate compiled artifacts of a would-be declarer who intentionally aborted (e.g. insufficient balance) before broadcasting, undermining the assumption that class content availability implies declaration.

### Likelihood Explanation
Trivial to trigger: any unprivileged user can submit a Declare transaction with a valid Sierra program but a signature/fee/nonce guaranteed to fail stateful validation, causing compilation and storage to occur first. The `declare_compilation_semaphore` limits concurrency but not total volume over time, so repeated attempts still accumulate storage.

### Recommendation
Do not persist compiled classes to durable class-manager storage until the originating Declare transaction has passed full stateful validation (and ideally not until block inclusion), or add a distinctly-scoped/quarantined write path with TTL-based eviction that a background job clears if the associated transaction is rejected. Additionally, gate `get_sierra`/`get_executable` responses on-chain declaration status (similar to the check already performed correctly in `apollo_state_reader/src/apollo_state.rs`'s `is_declared`) for any consumer that should only see finalized classes.

### Proof of Concept
1. Submit a Declare v3 transaction with a valid, large Sierra class but an intentionally invalid signature or insufficient account balance.
2. Observe (via `crates/apollo_gateway/src/gateway.rs:253-266`) that `convert_rpc_tx_to_internal_and_executable_txs` triggers `ClassManager::add_class`, compiling and calling `FsClassStorage::set_class` before `stateful_transaction_validator.extract_state_nonce_and_run_validations` runs and rejects the transaction.
3. Repeat with many distinct Sierra classes to accumulate unbounded disk usage on every sequencer node, or query `ClassManagerClient::get_sierra`/`get_executable` with the resulting class hash to retrieve the never-declared class content. [7](#0-6)

### Citations

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

**File:** crates/apollo_gateway_config/src/config.rs (L27-30)
```rust
// Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during
// transaction ingestion before the transaction's signature and balance are verified. Bound the
// number of compilations running concurrently to protect the node from resource exhaustion.
//
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

**File:** crates/apollo_class_manager/src/class_manager.rs (L115-126)
```rust
    #[instrument(skip(self), err)]
    pub fn get_executable(
        &self,
        class_id: ClassId,
    ) -> ClassManagerResult<Option<RawExecutableClass>> {
        Ok(self.classes.get_executable(class_id)?)
    }

    #[instrument(skip(self), err)]
    pub fn get_sierra(&self, class_id: ClassId) -> ClassManagerResult<Option<RawClass>> {
        Ok(self.classes.get_sierra(class_id)?)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L524-551)
```rust
    #[instrument(skip(self), level = "debug", err)]
    fn get_sierra(&self, class_id: ClassId) -> Result<Option<RawClass>, Self::Error> {
        if !self.contains_class(class_id)? {
            return Ok(None);
        }

        let path = self.get_sierra_path(class_id);
        let class =
            RawClass::from_file(path)?.ok_or(FsClassStorageError::ClassNotFound { class_id })?;

        Ok(Some(class))
    }

    #[instrument(skip(self), level = "debug", err)]
    fn get_executable(&self, class_id: ClassId) -> Result<Option<RawExecutableClass>, Self::Error> {
        let path = if self.contains_class(class_id)? {
            self.get_executable_path(class_id)
        } else if self.contains_deprecated_class(class_id) {
            self.get_deprecated_executable_path(class_id)
        } else {
            // Class does not exist in storage.
            return Ok(None);
        };

        let class = RawExecutableClass::from_file(path)?
            .ok_or(FsClassStorageError::ClassNotFound { class_id })?;
        Ok(Some(class))
    }
```

**File:** crates/apollo_class_manager/src/communication.rs (L1-63)
```rust
use apollo_class_manager_types::{
    ClassManagerRequest,
    ClassManagerRequestLabelValue,
    ClassManagerResponse,
};
use apollo_infra::component_definitions::ComponentRequestHandler;
use apollo_infra::component_server::{ConcurrentLocalComponentServer, RemoteComponentServer};
use apollo_infra::requests::LABEL_NAME_REQUEST_VARIANT;
use apollo_metrics::generate_permutation_labels;
use async_trait::async_trait;

use crate::ClassManager;

pub type LocalClassManagerServer =
    ConcurrentLocalComponentServer<ClassManager, ClassManagerRequest, ClassManagerResponse>;
pub type RemoteClassManagerServer =
    RemoteComponentServer<ClassManagerRequest, ClassManagerResponse>;

#[async_trait]
impl ComponentRequestHandler<ClassManagerRequest, ClassManagerResponse> for ClassManager {
    async fn handle_request(&mut self, request: ClassManagerRequest) -> ClassManagerResponse {
        let dynamic_config: apollo_class_manager_config::config::ClassManagerDynamicConfig = self
            .0
            .config_manager_client
            .get_class_manager_dynamic_config()
            .await
            .expect("Should be able to get class manager dynamic config");
        self.0.update_dynamic_config(dynamic_config);

        match request {
            ClassManagerRequest::AddClass(class) => {
                ClassManagerResponse::AddClass(self.0.add_class(class).await)
            }
            ClassManagerRequest::AddClassAndExecutableUnsafe(
                class_id,
                class,
                executable_class_hash_v2,
                executable_class,
            ) => ClassManagerResponse::AddClassAndExecutableUnsafe(
                self.0.add_class_and_executable_unsafe(
                    class_id,
                    class,
                    executable_class_hash_v2,
                    executable_class,
                ),
            ),
            ClassManagerRequest::AddDeprecatedClass(class_id, class) => {
                ClassManagerResponse::AddDeprecatedClass(
                    self.0.add_deprecated_class(class_id, class),
                )
            }
            ClassManagerRequest::GetExecutable(class_id) => {
                ClassManagerResponse::GetExecutable(self.0.get_executable(class_id))
            }
            ClassManagerRequest::GetSierra(class_id) => {
                ClassManagerResponse::GetSierra(self.0.get_sierra(class_id))
            }
            ClassManagerRequest::GetExecutableClassHashV2(class_id) => {
                let result = self.0.get_executable_class_hash_v2(class_id);
                ClassManagerResponse::GetExecutableClassHashV2(result)
            }
        }
    }
```
