### Title
Untrusted Cairo Native compiled code is `dlopen`'d and executed in-process from attacker-declared Sierra classes - ([File: crates/blockifier/src/execution/native/entry_point_execution.rs])

### Summary
When Cairo Native execution is enabled (`cairo_native_mode` = `WaitOnCompilation`/`LazyCompilation`), any attacker-submitted `DECLARE` transaction's Sierra class is compiled to a native machine-code shared object and later loaded and executed directly inside the sequencer process, mirroring the CVE-2026-24151 pattern of "loading a maliciously crafted input" leading to RCE.

### Finding Description
An unprivileged declarer submits a Sierra contract class through the gateway. Once accepted, `ClassManager::add_class` and the `NativeClassManager` pipeline compile the Sierra program to native code via `SierraToNativeCompiler::compile`, which shells out to the `cairo-native` compiler binary and writes the compiled artifact to a temp file [1](#0-0) . The only isolation applied to this untrusted-input compilation step is CPU/memory/file-size `ResourceLimits` on the child process [2](#0-1)  — there is no additional sandboxing (e.g., seccomp, namespaces) of the compiled output before it is trusted.

The resulting file is loaded via `AotContractExecutor::from_path`, i.e., a dynamic library produced from attacker-controlled Sierra bytecode is `dlopen`'d directly by the sequencer [3](#0-2) . That executor is stored as `NativeCompiledClassV1` and later invoked in-process for every call to the contract: `compiled_class.executor.run(...)` executes the natively compiled, attacker-influenced machine code with the sequencer's own privileges and address space during ordinary transaction execution [4](#0-3) . This is the same execution path reached from `execute_entry_point_call` for any `RunnableCompiledClass::V1Native` [5](#0-4) .

Because the codegen backend (cairo-native/MLIR/LLVM) and its generated code run un-sandboxed inside the sequencer process, any memory-safety bug in the compiler's generated machine code, its runtime library, or the syscall-handler FFI boundary (`NativeSyscallHandler`) is directly reachable by a single, unprivileged `DECLARE` + entry-point call from any external user — precisely analogous to the Megatron-LM "malicious input load" RCE pattern, except here the "input" is a Sierra class turned into native machine code that the node itself executes.

### Impact Explanation
Successful exploitation gives arbitrary code execution inside the sequencer node's process, which can lead to state corruption, incorrect execution results diverging from honest nodes (wrong committed state root / block hash), leakage of node secrets (staking/consensus keys), or a crash that halts block production — all reachable from a permissionless `DECLARE` transaction plus a subsequent invocation, matching the CVSS 7.8 High severity of the source CVE.

### Likelihood Explanation
Likelihood depends on Cairo Native being enabled in the deployment (`cairo_native_mode != Off`) and on the existence of a memory-safety bug in the `cairo_native` compiler/runtime or in the `NativeSyscallHandler` FFI glue reachable from attacker-controlled Sierra. The architecture itself provides only compile-time resource limiting, not execution-time sandboxing of the generated code, so once such a bug exists in the dependency, the sequencer path described here provides full, unauthenticated reachability with a single transaction.

### Recommendation
- Execute Cairo Native `AotContractExecutor` code in a hardened sandbox (seccomp-bpf, gVisor, or a separate isolated process/VM) rather than dlopen'ing and running attacker-derived native code directly inside the main sequencer process.
- Harden the syscall handler FFI boundary (`NativeSyscallHandler`) with strict bounds/type validation on all data crossing from native code back into Rust.
- Continuously fuzz the cairo-native codegen and runtime against adversarial Sierra programs, and pin/audit the `cairo_native` dependency version tightly.
- Consider defense-in-depth: run native execution with reduced OS privileges/capabilities and resource cgroups distinct from the consensus-critical process.

### Proof of Concept
1. Deploy/enable a sequencer node with `gateway_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode` set to `wait_on_compilation` or `lazy_compilation` (a supported production configuration) [6](#0-5) .
2. As an unprivileged user, submit a `DECLARE` transaction whose Sierra program is crafted to trigger a known/hypothetical miscompilation or memory-safety bug in `cairo-native`'s codegen (e.g., an out-of-bounds array access pattern or builtin-cost/gas accounting edge case abused to corrupt executor memory).
3. Once declared, invoke an entry point on the class; the sequencer compiles (`SierraToNativeCompiler::compile`) and later executes it via `compiled_class.executor.run(...)` in-process [4](#0-3) , triggering the memory-safety violation with the sequencer's own execution privileges.

### Citations

**File:** crates/apollo_compile_to_native/src/compiler.rs (L34-61)
```rust
    pub fn compile(
        &self,
        contract_class: ContractClass,
    ) -> Result<AotContractExecutor, CompilationUtilError> {
        let compiler_binary_path = &self.path_to_binary;

        let output_file = NamedTempFile::new()?;
        let output_file_path = output_file.path().to_str().ok_or(
            CompilationUtilError::UnexpectedError("Failed to get output file path".to_owned()),
        )?;
        let optimization_level = self.config.optimization_level.to_string();
        let additional_args = [output_file_path, "--opt-level", &optimization_level];
        let resource_limits = ResourceLimits::new(
            Some(self.config.max_cpu_time),
            self.config.max_file_size,
            Some(self.config.max_memory_usage),
        );
        let _stdout = compile_with_args(
            compiler_binary_path,
            contract_class,
            &additional_args,
            resource_limits,
        )?;

        Ok(AotContractExecutor::from_path(output_file.path())
            .map_err(|e| CompilationUtilError::CompilationError(e.to_string()))?
            .unwrap())
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

**File:** crates/blockifier/src/execution/native/entry_point_execution.rs (L60-66)
```rust
    let execution_result = compiled_class.executor.run(
        entry_point.selector.0,
        &syscall_handler.base.call.calldata.0.clone(),
        call_initial_gas,
        Some(builtin_costs),
        &mut syscall_handler,
    );
```

**File:** crates/blockifier/src/execution/execution_utils.rs (L146-167)
```rust
        #[cfg(feature = "cairo_native")]
        RunnableCompiledClass::V1Native(compiled_class) => {
            if context.tracked_resource_stack.last() == Some(&TrackedResource::CairoSteps)
                && !cfg!(feature = "only-native")
            {
                // We cannot run native with cairo steps as the tracked resources (it's a vm
                // resource).
                entry_point_execution::execute_entry_point_call(
                    call,
                    compiled_class.casm(),
                    state,
                    context,
                )
            } else {
                native_entry_point_execution::execute_entry_point_call(
                    call,
                    compiled_class,
                    state,
                    context,
                )
            }
        }
```

**File:** crates/apollo_node/resources/config_schema.json (L3012-3016)
```json
  "gateway_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode": {
    "description": "Cairo native execution mode. 'off' disables native execution, 'wait_on_compilation' compiles synchronously, and 'lazy_compilation' compiles asynchronously.",
    "privacy": "Public",
    "value": "off"
  },
```
