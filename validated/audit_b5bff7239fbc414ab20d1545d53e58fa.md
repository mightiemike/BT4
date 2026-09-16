I now have sufficient evidence to write the finding.

### Title
Untrusted declared Sierra classes compiled to native machine code and executed directly in-process (no sandbox at execution time) - ([File: crates/blockifier/src/execution/native/entry_point_execution.rs])

### Summary
Ghost's advisory concerns crafted, attacker-supplied content (a "theme") being processed by the server such that it results in arbitrary code execution on the host, because the artifact derived from untrusted input is trusted and executed with the application's full privileges. The closest reachable analog in this sequencer is the Cairo-Native execution pipeline: any account can declare an arbitrary Sierra class via a `DECLARE` transaction; when Cairo-Native mode is enabled (`wait_on_compilation` / `lazy_compilation`), the sequencer compiles that untrusted Sierra program into a native shared library and then, on execution, calls directly into that compiled machine code inside the main sequencer process — with no OS-level sandbox, container, or process isolation at execution time.

### Finding Description
Sierra-to-native compilation is invoked in `SierraToNativeCompiler::compile`, which shells out to a separate `cairo-native` compiler binary under `ResourceLimits` and writes the resulting artifact to a temp file [1](#0-0) . That resource-limited subprocess isolation only protects the *compilation* step. The resulting `AotContractExecutor` is loaded via `AotContractExecutor::from_path` and stored as-is inside `NativeCompiledClassV1` [2](#0-1) .

When a transaction later invokes that class, `execute_entry_point_call` in `crates/blockifier/src/execution/native/entry_point_execution.rs` calls `compiled_class.executor.run(...)` directly, executing the previously-compiled native machine code in the same OS process as the sequencer (batcher/gateway), sharing its memory space and privileges [3](#0-2) . This is dispatched from `execute_entry_point_call` in `execution_utils.rs`, which routes `RunnableCompiledClass::V1Native` calls straight into the native path whenever `cairo_native_mode` is not `Off` [4](#0-3) .

Unlike the Sierra→CASM path — which produces bytecode later interpreted by the Cairo VM (`cairo-vm`), a purpose-built, memory-safe, gas-metered interpreter — the native path produces and later executes real machine code compiled from attacker-controlled Sierra. Class declaration itself is gated only by `SierraToCasmCompiler`/`SierraCompiler` validation of the CASM representation (libfunc allow-list, bytecode-size limits) in `apollo_compile_to_casm` [5](#0-4) ; there is no analogous execution-time sandbox (seccomp, namespaces, separate process) for the native executor once it's loaded and cached in `NativeClassManager`/`RawClassCache` and invoked in-process for every subsequent call to that class hash [6](#0-5) .

If there exists any code-generation bug in `cairo-native`'s LLVM lowering of Sierra libfuncs that lets an attacker-crafted (but libfunc-allowlist-passing, since native mode bypasses the "audited only" restriction that CASM compilation can apply) Sierra program produce native code that violates type/memory-safety assumptions of the generated code (e.g., an out-of-bounds write, an unchecked cast, or a corrupted return continuation), that bug directly translates into memory corruption / arbitrary code execution within the sequencer process — this is architecturally the same trust class as Ghost's "malicious theme executes arbitrary code on the server," where developer-authored, semantically-restricted input (a theme / a Cairo1 contract) is trusted to produce safe executable artifacts and is run with the application's own privileges rather than in a constrained sandbox.

### Impact Explanation
A successful exploitation would let a single unprivileged `DECLARE` + invocation sequence achieve remote code execution on any full node/sequencer running with Cairo-Native enabled, since the native artifact executes with the process's privileges, sharing memory with wallet/consensus/other tenants' state within that node. This could lead to arbitrary state corruption, a wrong committed root/block hash (divergence from honest nodes not running Native, or from nodes with different compiler versions), denial of service of the node, or exfiltration of node secrets — all classified High/Critical impacts under the given validation criteria.

### Likelihood Explanation
Exploitability is gated by finding a genuine cairo-native code-generation bug reachable from valid (allow-listed) Sierra libfuncs — this is a non-trivial compiler-correctness bug class, not a simple configuration mistake. It is also gated by `cairo_native_mode` being non-`Off` (it defaults to `off` per config) [7](#0-6) ; nodes that keep native execution disabled are unaffected. Given this, likelihood is Medium — it requires an underlying miscompilation bug in the native codegen crate, whereas Ghost's issue only required a template-rendering escape by a plugin author.

### Recommendation
- Treat compiled native artifacts as untrusted even after successful compilation: run entry-point invocation of native executors inside a sandbox (seccomp-bpf, restricted namespace, or a separate worker process/VM) rather than in the sequencer's main process.
- Track and pin the exact `cairo-native` and LLVM toolchain versions used for both compilation and consensus-critical decisions, and fuzz/audit the codegen backend for the audited/allowed libfunc set specifically for memory-safety violations.
- Consider disabling native execution on any node whose crash/compromise would be consensus- or fund-critical until execution-time isolation is added, or restrict native execution to a class allow-list (`NativeClassesWhitelist`) of well-audited contracts rather than any declared class.

### Proof of Concept
Conceptual (compiler-bug-dependent) PoC steps:
1. Enable Cairo-Native (`cairo_native_run_config.cairo_native_mode = wait_on_compilation` or `lazy_compilation`) on a target node.
2. Craft a Sierra program using only allow-listed libfuncs that, when lowered to native code by `cairo-native`, triggers a memory-safety violation in the generated machine code (e.g., a libfunc combination causing an out-of-bounds buffer write due to a codegen bug).
3. Submit a `DECLARE` transaction with this Sierra class; the node compiles it via `SierraToNativeCompiler::compile` and caches it as `NativeCompiledClassV1` [8](#0-7) .
4. Submit an `INVOKE` transaction calling the malicious entry point; `execute_entry_point_call` runs the native executor in-process [3](#0-2) , triggering the memory-safety violation and achieving code execution with the sequencer process's privileges.

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

**File:** crates/blockifier/src/execution/native/contract_class.rs (L72-82)
```rust
#[derive(Debug)]
pub struct NativeCompiledClassV1Inner {
    pub executor: AotContractExecutor,
    casm: CompiledClassV1,
}

impl NativeCompiledClassV1Inner {
    fn new(executor: AotContractExecutor, casm: CompiledClassV1) -> Self {
        NativeCompiledClassV1Inner { executor, casm }
    }
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

**File:** crates/blockifier/src/execution/execution_utils.rs (L143-167)
```rust
        RunnableCompiledClass::V1(compiled_class) => {
            entry_point_execution::execute_entry_point_call(call, compiled_class, state, context)
        }
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

**File:** crates/blockifier/src/state/native_class_manager.rs (L124-153)
```rust
    /// Returns the runnable compiled class for the given class hash, if it exists in class_cache.
    pub fn get_runnable(
        &self,
        class_hash: &ClassHash,
        native_classes_whitelist: &NativeClassesWhitelist,
    ) -> Option<RunnableCompiledClass> {
        let cached_class = self.class_cache.get(class_hash)?;
        if let CompiledClasses::V1(..) = cached_class {
            // When native mode is WaitOnCompilation, all V1 classes should have been
            // compiled to native synchronously. A V1 cache entry indicates a pipeline bug.
            assert_ne!(
                self.cairo_native_mode(),
                CairoNativeMode::WaitOnCompilation,
                "Manager did not wait on native compilation."
            );
        }

        let cached_class = match cached_class {
            CompiledClasses::V1Native(CachedCairoNative::Compiled(native))
                if !native_classes_whitelist.contains(class_hash) =>
            {
                CompiledClasses::into_non_native_class(native)
            }
            CompiledClasses::V1Native(..) | CompiledClasses::V1(..) | CompiledClasses::V0(..) => {
                cached_class
            }
        };

        Some(cached_class.to_runnable())
    }
```

**File:** crates/blockifier/src/state/native_class_manager.rs (L273-303)
```rust
/// Processes a compilation request and caches the result.
fn process_compilation_request(
    class_cache: RawClassCache,
    compiler: Arc<SierraToNativeCompiler>,
    compilation_request: CompilationRequest,
    panic_on_compilation_failure: bool,
) -> Result<(), CompilationUtilError> {
    let (class_hash, sierra, casm) = compilation_request;
    if let Some(CompiledClasses::V1Native(_)) = class_cache.get(&class_hash) {
        // The contract class is already compiled to native - skip the compilation.
        return Ok(());
    }
    let sierra_for_compilation = into_contract_class_for_compilation(sierra.as_ref());
    let start = Instant::now();
    let compilation_result = compiler.compile(sierra_for_compilation);
    let duration = start.elapsed();
    log::info!(
        "Compiling to native contract with class hash: {:#066x}. Duration: {:.3} seconds",
        class_hash.0,
        duration.as_secs_f32()
    );
    match compilation_result {
        Ok(executor) => {
            let native_compiled_class = NativeCompiledClassV1::new(executor, casm);
            class_cache.set(
                class_hash,
                CompiledClasses::V1Native(CachedCairoNative::Compiled(native_compiled_class)),
            );
            log::info!("Compilation succeeded");
            Ok(())
        }
```

**File:** crates/apollo_node/resources/config_schema.json (L247-251)
```json
  "batcher_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode": {
    "description": "Cairo native execution mode. 'off' disables native execution, 'wait_on_compilation' compiles synchronously, and 'lazy_compilation' compiles asynchronously.",
    "privacy": "Public",
    "value": "off"
  },
```
