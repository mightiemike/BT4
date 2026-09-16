## Title
Sierra-to-CASM compilation runs a synchronous blocking subprocess call directly on the async runtime, ahead of signature/balance verification - (File: crates/apollo_compile_to_casm/src/compiler.rs, crates/apollo_compile_to_casm/src/communication.rs)

### Summary
An unauthenticated transaction sender can submit a `Declare` transaction whose Sierra program is expensive to compile. The gateway triggers Sierra→CASM compilation before the transaction's signature and balance/fee are verified, and the compilation itself is executed as a genuine OS-level blocking call (`Command::output()`) directly inside an `async fn` request handler that runs on a Tokio worker task, not via `spawn_blocking`. This mirrors the Zebra advisory pattern: an expensive, non-preemptible synchronous operation reachable pre-authentication, gated only by a concurrency counter rather than being isolated off the async runtime.

### Finding Description
The gateway's `add_tx_inner` runs cheap stateless checks and then triggers compilation for `Declare` transactions before stateful validation (which performs nonce/balance/fee and signature checks): [1](#0-0) 

The `SierraCompilationConfig`/gateway comment explicitly documents that "Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during transaction ingestion **before the transaction's signature and balance are verified**": [2](#0-1) 

The compilation call chain ends in `SierraToCasmCompiler::compile`, which invokes `compile_with_args`, a real subprocess spawn-and-wait (`Command::new(...).output()`), i.e., a synchronous blocking syscall that blocks the calling thread until the child process exits (bounded by `max_cpu_time`, but that can be several seconds): [3](#0-2) [4](#0-3) 

Critically, this call is made from the `SierraCompiler` component's `handle_request`, an `async fn` that is executed as a plain `tokio::spawn`ed task inside `ConcurrentLocalComponentServer::process_requests` — not offloaded via `spawn_blocking`: [5](#0-4) [6](#0-5) 

This is the same bug class as the Zebra advisory: a synchronous, CPU-bound operation (there, C++ FFI script verification; here, a subprocess fork/exec/wait for compilation) executes directly on an async runtime worker rather than a dedicated blocking thread, so it cannot be preempted or cancelled by the runtime once started. The only mitigations present are counting semaphores — `declare_compilation_semaphore` (default 40) at the gateway and `max_concurrency` (default 128) at the `SierraCompiler`'s `ConcurrentLocalComponentServer` — which bound the *number* of concurrent compilations but do not prevent each one from pinning a Tokio worker for its full duration: [7](#0-6) [8](#0-7) 

The gateway itself does perform a rate-limiting semaphore for declares (`try_acquire`, immediate rejection when exhausted), which is a genuine mitigation not present in the Zebra case. However, the deeper `SierraCompiler` component (and other deployment topologies where it is a standalone service reachable independently, or where its `max_concurrency` of 128 well exceeds the gateway's 40-permit cap) still performs the actual blocking work on the async executor.

### Impact Explanation
If enough concurrent `Declare` transactions with maximally-sized/complex-but-valid-looking Sierra programs are submitted (within the `max_bytecode_size` / other config limits), each occupies a Tokio worker thread for up to `max_cpu_time` seconds while a subprocess runs, without yielding to the runtime. In deployment configurations where the sierra compiler shares a process/runtime with other latency-sensitive duties, or simply within its own dedicated Tokio runtime, saturating the small pool of async worker threads (Tokio typically sizes worker threads close to the CPU count) can stall the compiler's own request loop, RPC handling for other requests in flight, and — because it can be reached before signature/balance checks — allows resource consumption from senders who have not yet proven authorization for the class hash paid via that transaction. This is a resource-exhaustion/liveness degradation vector rather than a fund-loss or state-correctness bug.

### Likelihood Explanation
Reachability is straightforward: any account can submit `Declare` transactions with valid-but-heavy Sierra programs up to configured size limits (`max_bytecode_size`), and no signature or balance check gates the compilation step. However, exploitation is bounded by: (1) the gateway's `declare_compilation_semaphore` (default 40) which immediately rejects excess concurrent declares rather than queuing them, (2) OS-level `ResourceLimits` (CPU time, memory, file size) applied via `rlimit`/`pre_exec` to each compiler subprocess, which caps the worst-case blocking duration per call, and (3) fee/balance requirements that still apply economic cost to spamming declare transactions (each declare transaction must still be a well-formed, fee-paying transaction to be processed this far). These existing mitigations reduce likelihood relative to the original Zebra report, where a peer could submit unlimited transactions for free before any per-peer accounting.

### Recommendation
- Run `SierraToCasmCompiler::compile` (and the analogous native compiler) via `tokio::task::spawn_blocking` inside `SierraCompiler::compile`'s call site, so the subprocess wait cannot occupy the async runtime's worker threads.
- Alternatively/dedicate a bounded blocking thread pool sized independently from the async runtime's worker count for compilation subprocess calls.
- Ensure the `declare_compilation_semaphore` bound at the gateway is always ≤ the `SierraCompiler` component's own `max_concurrency`, so the gateway's own protection is the binding constraint everywhere the compiler is deployed.
- Consider moving cheap, purely-static checks on the Sierra program (e.g., stricter pre-compilation size/complexity heuristics) ahead of compilation, and signature verification ahead of compilation where feasible, to reduce the value of triggering compilation for unauthenticated or invalid transactions.

### Proof of Concept
1. Craft a `Declare` transaction with a Sierra program near the `max_bytecode_size` and `max_cpu_time` limits (`SierraCompilationConfig`) such that compilation takes close to the maximum allowed CPU time.
2. Submit up to `max_concurrent_declare_compilations` (default 40) such transactions concurrently to the gateway; each triggers `ClassManager::add_class` → `SierraCompiler::compile` → `SierraToCasmCompiler::compile` → `compile_with_args`, each blocking a Tokio worker thread in the `SierraCompiler` component for the duration of the subprocess call [9](#0-8) [3](#0-2) .
3. Observe increased latency/backlog in the `SierraCompiler` component's request processing and any other requests sharing its Tokio runtime, for the duration of the compilations, before signature/balance checks would have rejected an otherwise-invalid transaction.

Note: I was unable to fully confirm within the available context whether `stateless_tx_validator.validate` performs signature verification prior to compilation (the grep for signature terms in `stateless_transaction_validator.rs` returned matches I could not inspect in this final pass) — this should be verified directly in `crates/apollo_gateway/src/stateless_transaction_validator.rs` and `crates/apollo_gateway/src/stateful_transaction_validator.rs` to precisely determine which checks (signature vs. balance/nonce) occur before vs. after compilation.

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

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L30-55)
```rust
    pub fn compile(
        &self,
        contract_class: ContractClass,
    ) -> Result<CasmContractClass, CompilationUtilError> {
        let compiler_binary_path = &self.path_to_binary;
        let additional_args = &[
            "--add-pythonic-hints",
            "--max-bytecode-size",
            &self.config.max_bytecode_size.to_string(),
            "--allowed-libfuncs-list-name",
            if self.config.audited_libfuncs_only { "audited" } else { "all" },
        ];
        let resource_limits = ResourceLimits::new(
            Some(self.config.max_cpu_time),
            None,
            Some(self.config.max_memory_usage),
        );

        let stdout = compile_with_args(
            compiler_binary_path,
            contract_class,
            additional_args,
            resource_limits,
        )?;
        Ok(serde_json::from_slice::<CasmContractClass>(&stdout)?)
    }
```

**File:** crates/apollo_compilation_utils/src/compiler_utils.rs (L17-50)
```rust
pub fn compile_with_args(
    compiler_binary_path: &Path,
    contract_class: ContractClass,
    additional_args: &[&str],
    resource_limits: ResourceLimits,
) -> Result<Vec<u8>, CompilationUtilError> {
    // Create a temporary file to store the Sierra contract class.
    let serialized_contract_class = serde_json::to_string(&contract_class)?;

    let mut temp_file = NamedTempFile::new()?;
    temp_file.write_all(serialized_contract_class.as_bytes())?;
    let temp_file_path = temp_file.path().to_str().ok_or(CompilationUtilError::UnexpectedError(
        "Failed to get temporary file path".to_owned(),
    ))?;

    // Set the parameters for the compile process.
    let mut command = Command::new(compiler_binary_path.as_os_str());
    command.arg(temp_file_path).args(additional_args);

    // Apply the resource limits to the command.
    resource_limits.apply(&mut command);

    // Run the compile process.
    let compile_output = command.output()?;

    if !compile_output.status.success() {
        let stderr_output = String::from_utf8(compile_output.stderr)
            .unwrap_or_else(|_| "Failed to decode stderr output".to_string());

        let error_message = format_compiler_error(&stderr_output, &compile_output.status);
        return Err(CompilationUtilError::CompilationError(error_message));
    }
    Ok(compile_output.stdout)
}
```

**File:** crates/apollo_compile_to_casm/src/communication.rs (L17-27)
```rust
#[async_trait]
impl ComponentRequestHandler<SierraCompilerRequest, SierraCompilerResponse> for SierraCompiler {
    async fn handle_request(&mut self, request: SierraCompilerRequest) -> SierraCompilerResponse {
        match request {
            SierraCompilerRequest::Compile(contract_class) => {
                let compilation_result =
                    self.compile(contract_class).map_err(SierraCompilerError::from);
                SierraCompilerResponse::Compile(compilation_result)
            }
        }
    }
```

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L306-355)
```rust
    async fn process_requests(&mut self) {
        let component_name = short_type_name::<Component>();
        info!(
            "Starting to process requests in the component {component_name} concurrent local \
             server",
        );

        let RequestProcessingMembers {
            component,
            mut high_rx,
            mut normal_rx,
            metrics,
            processing_time_warning_threshold_ms,
        } = self.local_component_server.get_processing_inner_members();

        let task_limiter = Arc::new(Semaphore::new(self.max_concurrency));

        tokio::spawn(async move {
            loop {
                // TODO(Tsabary): add a test for the queueing time metric.
                let (request, tx, request_id) = get_next_request_for_processing(
                    &mut high_rx,
                    &mut normal_rx,
                    &component_name,
                    metrics,
                )
                .await;

                // Acquire a permit to run the task.
                let permit = task_limiter.clone().acquire_owned().await.unwrap();

                // Clone the component for concurrent request processing.
                let mut cloned_component = component.clone();
                tokio::spawn(async move {
                    process_request(
                        &mut cloned_component,
                        request,
                        request_id,
                        tx,
                        metrics,
                        processing_time_warning_threshold_ms,
                    )
                    .await;
                    // Drop the permit to allow more tasks to be created.
                    drop(permit);
                });
            }
        });
    }
}
```

**File:** crates/apollo_node/resources/config_schema.json (L2067-2071)
```json
  "components.sierra_compiler.local_server_config.max_concurrency": {
    "description": "The maximum number of concurrent requests handling.",
    "privacy": "Public",
    "value": 128
  },
```
