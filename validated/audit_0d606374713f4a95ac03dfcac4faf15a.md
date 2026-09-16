Confirmed order of operations from `crates/apollo_gateway/src/gateway.rs` `add_tx_inner`: stateless validation (no signature verification, no balance/fee check) → Sierra-to-CASM compilation for Declare transactions (`convert_rpc_tx_to_internal_and_executable_txs`) → *only afterward* stateful validation (nonce, resource-bounds/fee, `__validate__`, mempool admission).

### Title
Unmetered Sierra-to-CASM Compilation Before Fee/Signature Validation Enables Gateway CPU-Exhaustion DoS via Repeated Declare Submissions - (File: `crates/apollo_gateway/src/gateway.rs`)

### Summary
The CVE describes an authenticated Cassandra client causing latency DoS by repeatedly triggering expensive password re-hashing (`ALTER ROLE`) before the cost of that operation is properly throttled per-request. The analogous pattern in this repo is that `Gateway::add_tx_inner` runs the CPU/memory-intensive Sierra→CASM compilation step for every `Declare` transaction *before* any signature verification or fee/balance validation takes place. The only safeguard is a global concurrency semaphore (`declare_compilation_semaphore`, default 40), not a per-account/fee-gated cost check, so a sender can force full compiler invocations repeatedly at negligible cost to themselves.

### Finding Description
In `add_tx_inner` (`crates/apollo_gateway/src/gateway.rs:214-299`):
1. `self.stateless_tx_validator.validate(&tx)` runs only structural/format checks — no signature check, no balance check. [1](#0-0) 
2. For `Declare` transactions, a concurrency permit is acquired and `convert_rpc_tx_to_internal_and_executable_txs` is called, which drives Sierra→CASM compilation via the class manager/compiler before stateful validation runs. [2](#0-1) 
3. Only *after* compilation completes does `stateful_transaction_validator.extract_state_nonce_and_run_validations` check nonce, resource bounds/fee sufficiency, and run `__validate_declare__` (which verifies the signature). [3](#0-2) [4](#0-3) 

The code's own comment acknowledges the compilation is CPU/memory intensive and happens before fee/signature checks, and mitigates only the *concurrent* blast radius, not the *aggregate* cost an attacker can impose sequentially or across accounts: [5](#0-4) 

Because `ClassManager::add_class` only skips recompilation for a class hash it has already cached, an attacker can trivially defeat this cache by submitting distinct Sierra programs (each near the configured `max_contract_bytecode_size`/`max_contract_class_object_size` limits) with valid syntax but an invalid signature or insufficient fee — each one forces a full, expensive Sierra→CASM compilation before the transaction is ultimately rejected in stateful validation. [6](#0-5) 

This mirrors the CVE-2026-32588 pattern precisely: an "authenticated" (network-admitted, non-privileged) actor can cause the server to perform repeated, uncached, expensive cryptographic/compilation work whose cost is not gated by the resource/fee mechanism meant to price it, degrading service for all other transaction senders sharing the gateway/compiler fleet.

### Impact Explanation
Each malicious Declare transaction consumes a compiler-subprocess CPU/memory budget (bounded by `SierraCompilationConfig`'s `max_cpu_time`/`max_memory_usage` per call, but not by account reputation, balance, or per-account rate limiting) before being rejected for insufficient fee or an invalid signature. Because the fee/balance/signature check occurs strictly after compilation, the attacker pays nothing for compilation attempts that are ultimately rejected. Sustained submission of such transactions (bounded only by the shared `max_concurrent_declare_compilations` semaphore across the whole gateway fleet) increases end-to-end declare/ingestion latency and consumes the sierra-compiler fleet's capacity, degrading the network's ability to admit legitimate transactions in a timely manner — a form of network-wide latency DoS analogous to the CVE, reachable by any transaction sender without requiring a bootstrap/staking role.

### Likelihood Explanation
Likelihood is moderate-to-high: the attack requires no special privilege, only the ability to submit an RPC `Declare` transaction with syntactically valid Sierra bytecode near size limits and an arbitrary/incorrect signature or insufficient resource bounds. No balance is strictly required to pay compilation cost since the fee check happens after compilation. The `authorized_declarer_accounts` allowlist and `block_declare` flag can restrict this in specific deployments but are optional (default: unrestricted, `None`), so on a standard configuration the attack surface is fully open to any address. [7](#0-6) 

### Recommendation
Move (or duplicate a cheap pre-check of) signature/fee-sufficiency validation ahead of the Sierra→CASM compilation step, or require a compilation-cost deposit / rate-limit compilation attempts per sending address (not just a global concurrency cap), so that repeated invalid/underfunded Declare submissions cannot force unmetered compiler invocations.

### Proof of Concept
1. Craft many distinct Sierra contract classes, each just under `max_contract_bytecode_size` / `max_contract_class_object_size`, so each has a unique class hash (defeating the class-manager cache).
2. For each, build a `Declare` V3 RpcTransaction with a syntactically valid structure but either (a) an incorrect/garbage signature, or (b) resource bounds just above the stateless minimum but insufficient to cover the true fee, and submit via `add_tx`.
3. Observe that each submission passes `stateless_tx_validator.validate` and proceeds to acquire the declare-compilation semaphore and invoke the full Sierra→CASM compiler (`SierraCompiler::compile` → `SierraToCasmCompiler::compile`), before ultimately failing at `extract_state_nonce_and_run_validations` (bad signature / insufficient fee).
4. Repeating this (sequentially or up to `max_concurrent_declare_compilations` in parallel, from one or many addresses) drives sustained CPU/memory load on the sierra-compiler fleet and increases gateway ingestion latency for legitimate transactions, with no fee ever charged to the attacker for the compilation work performed.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L235-236)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L240-255)
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

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
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

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-90)
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
```
