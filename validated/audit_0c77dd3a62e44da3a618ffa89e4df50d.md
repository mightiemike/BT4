### Title
Sierra-to-Native compilation path omits the audited-libfuncs allowlist enforced by the CASM compiler, allowing declared classes to reach in-process native code execution unrestricted - (File: crates/apollo_compile_to_native/src/compiler.rs)

### Summary
The CASM compiler (`SierraToCasmCompiler::compile`) explicitly restricts Sierra compilation to the `audited` libfuncs list unless `audited_libfuncs_only` is disabled, passing `--allowed-libfuncs-list-name` to the compiler subprocess. [1](#0-0) 
The parallel Sierra-to-Native compiler (`SierraToNativeCompiler::compile`), which is used when Cairo Native execution is enabled, has no equivalent restriction: its `SierraCompilationConfig` (in `apollo_compile_to_native_types`) has no `audited_libfuncs_only` field at all, and its `additional_args` never include a libfuncs allowlist flag. [2](#0-1) [3](#0-2) 

### Finding Description
The Mesos advisory's core bug class is that untrusted, attacker-supplied content (a Docker image) was processed by a runtime component without enforcing the isolation/allowlisting the runtime was designed to guarantee, letting attacker content escalate to execution with elevated privilege.

In this sequencer, an analogous privilege boundary exists between the CASM execution path (Cairo VM interpreting bytecode, gated to audited libfuncs) and the Cairo Native path, where a declared class is AOT-compiled to native machine code and that machine code is then loaded and directly invoked in-process via `AotContractExecutor::run` inside `native_entry_point_execution::execute_entry_point_call`. [4](#0-3) 
The audited-libfuncs allowlist is the mechanism that is supposed to bound what Sierra libfuncs (and therefore what native codegen constructs) an untrusted declared class can use. That allowlist is enforced only in the CASM compilation subprocess invocation: [5](#0-4) 
but is completely absent from the native compilation invocation, which only passes an output path and optimization level: [6](#0-5) 
Consequently, once Cairo Native mode is enabled (`CairoNativeMode != Off`), a class declarer's Sierra program can use non-audited libfuncs when compiled to native, even though the same class would be restricted when compiled to CASM. Because the native artifact is executed directly as machine code in the sequencer/batcher process (not interpreted through the constrained Cairo VM), unaudited libfunc lowering to native code removes a defense-in-depth boundary that is explicitly relied upon elsewhere in the codebase (the CASM path).

### Impact Explanation
If an unaudited libfunc's native lowering has any codegen weakness (memory-safety bug, incorrect builtin cost accounting, unchecked FFI/syscall boundary, etc.), a class declarer can trigger it purely by declaring and invoking a contract, since the native code runs with the same privileges as the sequencer process (batcher/executor). This can lead to sequencer process crashes (denial of service against block production), execution-context memory corruption, or divergence between the CASM-executing honest nodes and native-executing nodes if the additional libfuncs are not fully vetted for native lowering — i.e., a network unable to reach consensus/confirm new transactions, or honest-node divergence, both of which are in-scope impacts.

### Likelihood Explanation
Reachable by any account able to submit a `DECLARE` transaction; no operator or protocol-level privilege is required. The trigger condition is that Cairo Native execution is enabled in the deployment (`CairoNativeMode != Off`), which is an operator configuration choice already present and exercised in this codebase (see `CairoNativeRunConfig`/`NativeClassesWhitelist` plumbing in `crates/blockifier/src/blockifier/config.rs` and `crates/apollo_batcher/src/batcher.rs`). Whether a given deployment restricts native compilation to a whitelist of classes (`NativeClassesWhitelist`) could reduce exploitability, but the compiler-level omission of the libfuncs restriction is a control-flow parity bug that exists independent of that whitelist and should be fixed regardless of whitelist scope, since the whitelist gates which *known* classes get native-compiled, not what fed-in classes are permitted to declare.

### Recommendation
Add an `audited_libfuncs_only` (or equivalent) field to `apollo_compile_to_native_types::SierraCompilationConfig`, mirroring `apollo_sierra_compilation_config::SierraCompilationConfig`, and pass the corresponding `--allowed-libfuncs-list-name` (or the Cairo Native compiler's equivalent flag) in `SierraToNativeCompiler::compile`'s `additional_args`, so native compilation enforces exactly the same libfunc allowlist boundary as CASM compilation before any declared class can reach in-process native execution.

### Proof of Concept
Conceptual PoC (requires a deployment with `CairoNativeMode != Off`):
1. Craft a Sierra contract class that uses a libfunc excluded from the "audited" list (one that would be rejected by CASM compilation's `--allowed-libfuncs-list-name audited` check in `crates/apollo_compile_to_casm/src/compiler.rs`).
2. Submit a `DECLARE` transaction for this class through the gateway.
3. Observe that when the sequencer compiles this class via `SierraToNativeCompiler::compile` (`crates/apollo_compile_to_native/src/compiler.rs`), no libfunc-list restriction is applied, and compilation succeeds, producing a native `AotContractExecutor`.
4. Invoke the declared contract; execution proceeds through `native_entry_point_execution::execute_entry_point_call`, directly running the attacker-influenced native code in-process — demonstrating that a boundary enforced for CASM execution is bypassed for native execution. [1](#0-0) [6](#0-5) [4](#0-3)

### Citations

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L34-46)
```rust
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
```

**File:** crates/apollo_compile_to_native/src/compiler.rs (L34-56)
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
```

**File:** crates/apollo_compile_to_native_types/src/lib.rs (L17-29)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct SierraCompilationConfig {
    /// Cairo Native file size limit (in bytes).
    pub max_file_size: Option<u64>,
    /// Compilation CPU time limit (in seconds).
    pub max_cpu_time: u64,
    /// Compilation process's virtual memory (address space) byte limit.
    pub max_memory_usage: u64,
    /// The level of optimization to apply during compilation.
    pub optimization_level: u8,
    /// Compiler binary path.
    pub compiler_binary_path: Option<PathBuf>,
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
