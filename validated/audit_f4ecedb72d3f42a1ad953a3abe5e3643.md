## Finding: Sierra-to-CASM/Native compilation subprocess blocks the tokio worker thread indefinitely with only a CPU-time rlimit (no wall-clock timeout), reachable via an unprivileged Declare transaction — (`File: crates/apollo_compilation_utils/src/compiler_utils.rs`)

### Summary
Every Declare transaction submitted by an ordinary account triggers a synchronous, blocking `Command::output()` call to compile the attacker-supplied Sierra program via an external binary. The only protection against a hanging subprocess is a CPU-time `rlimit` (`RLIMIT_CPU`), set via `ResourceLimits`. `RLIMIT_CPU` only measures **CPU time actually consumed**, not wall-clock time. There is no wall-clock timeout, no `tokio::time::timeout`, and the call is not off-loaded to `spawn_blocking`, so a subprocess that stalls without burning CPU (e.g., blocked in an internal deadlock, lock contention, or I/O wait inside the compiler binary) hangs the calling code forever, blocking the tokio worker thread that is running the request task.

### Finding Description
The compilation call chain reachable from a Declare transaction is:
- `apollo_gateway::gateway::GenericGateway::add_tx_inner` → `convert_rpc_tx_to_internal_and_executable_txs` → `TransactionConverter::convert_rpc_tx_to_internal` → `self.class_manager_client.add_class(tx.contract_class)` [1](#0-0) 
- `ClassManager::add_class` → `self.compiler.compile(class.clone()).await` [2](#0-1) 
- The Sierra compiler component's request handler calls the **synchronous** `compile()` method directly inside `async fn handle_request`, with no `spawn_blocking`/timeout: [3](#0-2) 
- `SierraToCasmCompiler::compile` builds `ResourceLimits` from only `max_cpu_time` (CPU seconds) and `max_memory_usage`, then calls `compile_with_args`: [4](#0-3) 
- `compile_with_args` runs the compiler binary with `command.output()`, a fully blocking call with **no wall-clock timeout at all**: [5](#0-4) 
- The only guard is `ResourceLimits`, which sets `RLIMIT_CPU`, `RLIMIT_FSIZE`, and `RLIMIT_AS` via `pre_exec`. `RLIMIT_CPU` is explicitly a CPU-time limit: [6](#0-5) 

The unit test for this limit even demonstrates that `RLIMIT_CPU` is only tripped by a CPU-spinning loop (`while true; do :; done`) — it says nothing about a process that is stalled/blocked without consuming CPU: [7](#0-6) 

Because `handle_request` calls the blocking `compile()`/`command.output()` synchronously (not via `tokio::task::spawn_blocking`), this hangs the actual OS thread backing the tokio task, not merely a lightweight virtual task. This is executed under `ConcurrentLocalComponentServer::process_requests`, which pulls a permit from a `Semaphore::new(max_concurrency)` (default 128) and spawns one task per in-flight request: [8](#0-7) 

If an attacker submits `max_concurrency` (128, or `max_concurrent_declare_compilations`=40 per gateway instance, times however many gateway instances forward to the compiler component) Declare transactions whose Sierra payloads cause the underlying `cairo-lang`/native compiler binary to stall (deadlock, blocked syscall, or any hang that doesn't consume CPU), each of those requests permanently occupies a tokio worker thread with no timeout, no cancellation and no reclamation — directly analogous to the reported CVE's "nil channel that blocks forever, with no timeout, no context cancellation, and no server-side reclamation." Once enough worker threads are pinned this way, the compiler component (and any other async work sharing its runtime) stops making progress, halting all further Declare (and, if compute-shared, other) transaction processing across the sequencer.

### Impact Explanation
This is reachable from a single unprivileged Declare transaction — no special privileges, no operator/proposer access needed. Enough concurrent malicious Declare submissions exhaust the compiler component's worker-thread capacity, freezing declare-transaction (and potentially broader) processing. This constitutes a network unable to process new transactions (of at least the Declare type, and potentially the whole gateway pipeline depending on runtime thread sharing), which matches the "network unable to confirm new transactions" acceptance criterion. Severity is Medium/High depending on deployment (dedicated vs. shared tokio runtime for the Sierra compiler service), consistent with the referenced CVE's Medium (6.0) rating for a similar unbounded-block DoS.

### Likelihood Explanation
Likelihood is moderate-to-high: an attacker only needs to construct a Sierra program that makes the `cairo-lang-starknet`/`cairo-native` compiler binary hang without consuming CPU (e.g., a pathological input that triggers an internal lock/deadlock, or a blocking wait unrelated to CPU cycles — such behavior is plausible in complex compiler toolchains handling adversarial/malformed input, similar to known compiler DoS classes). No source access to the compiler internals was reachable from this index to prove a specific hang trigger exists today, but the defensive gap itself (CPU-rlimit-only, no wall-clock timeout, synchronous blocking call inside the async request handler) is unambiguous and independently exploitable the moment any such hang condition is found in the compiler binary.

### Recommendation
- Wrap the compiler subprocess invocation in a hard wall-clock timeout (e.g., spawn the process, then race it against `tokio::time::timeout`/an OS-level timer, killing the process group on expiry) rather than relying solely on `RLIMIT_CPU`.
- Execute `compile()` via `tokio::task::spawn_blocking` so a stalled subprocess only ties up a blocking-pool thread (which can be sized/monitored independently) rather than a core async worker thread.
- Add an explicit "kill on timeout" watchdog around `Command::output()` in `compile_with_args`, and surface a clear compilation-timeout error to the gateway so it can reject the Declare transaction instead of hanging.

### Proof of Concept
1. Submit a Declare transaction whose Sierra `contract_class` payload is crafted to make the `cairo-lang-starknet` (or `cairo-native`) compiler binary stall without spending CPU (e.g., an input pattern known to trigger lock contention/blocking I/O inside the compiler process rather than a CPU-bound infinite loop).
2. `apollo_gateway` forwards it through `TransactionConverter::convert_rpc_tx_to_internal` → `ClassManager::add_class` → `SierraCompilerClient::compile`, landing in `SierraCompiler::handle_request` → `SierraToCasmCompiler::compile` → `compile_with_args` → `Command::output()`.
3. The child process hangs without consuming CPU; `RLIMIT_CPU` never fires; `command.output()` never returns; the tokio task/worker thread processing this request is blocked forever with no timeout.
4. Repeat concurrently up to the compiler component's `max_concurrency` (128) / gateway's `max_concurrent_declare_compilations` permits to pin all available worker threads, stalling further Declare transaction (and potentially broader) processing.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-350)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
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

**File:** crates/apollo_compile_to_casm/src/communication.rs (L19-26)
```rust
    async fn handle_request(&mut self, request: SierraCompilerRequest) -> SierraCompilerResponse {
        match request {
            SierraCompilerRequest::Compile(contract_class) => {
                let compilation_result =
                    self.compile(contract_class).map_err(SierraCompilerError::from);
                SierraCompilerResponse::Compile(compilation_result)
            }
        }
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

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_unix.rs (L41-76)
```rust
pub struct ResourceLimits {
    /// A limit (in seconds) on the amount of CPU time that the process can consume.
    cpu_time: Option<RLimit>,
    /// The maximum size (in bytes) of files that the process may create.
    file_size: Option<RLimit>,
    /// The maximum size (in bytes) of the process’s virtual memory (address space).
    memory_size: Option<RLimit>,
}

impl ResourceLimits {
    pub fn new(
        cpu_time: Option<u64>,
        file_size: Option<u64>,
        memory_size: Option<u64>,
    ) -> ResourceLimits {
        ResourceLimits {
            cpu_time: cpu_time.map(|t| RLimit {
                resource: Resource::CPU,
                soft_limit: t,
                hard_limit: t,
                units: "seconds".to_string(),
            }),
            file_size: file_size.map(|x| RLimit {
                resource: Resource::FSIZE,
                soft_limit: x,
                hard_limit: x,
                units: "bytes".to_string(),
            }),
            memory_size: memory_size.map(|y| RLimit {
                resource: Resource::AS,
                soft_limit: y,
                hard_limit: y,
                units: "bytes".to_string(),
            }),
        }
    }
```

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_test.rs (L10-23)
```rust
#[rstest]
fn test_cpu_time_limit() {
    let cpu_limit = 1; // 1 second
    let cpu_time_rlimit = ResourceLimits::new(Some(cpu_limit), None, None);

    let start = Instant::now();
    let mut command = Command::new("bash");
    command.args(["-c", "while true; do :; done;"]);
    cpu_time_rlimit.apply(&mut command);
    let status = command.spawn().expect("Failed to start CPU consuming process").wait().unwrap();
    assert!(start.elapsed().as_secs() <= cpu_limit);
    let signal = status.signal();
    assert_eq!(signal, Some(9), "Process should terminate with SIGKILL (9) got {signal:?}");
}
```

**File:** crates/apollo_infra/src/component_server/local_component_server.rs (L321-353)
```rust
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
```
