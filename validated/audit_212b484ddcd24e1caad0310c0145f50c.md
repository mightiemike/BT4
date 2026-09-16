## Title
Cairo Native AOT compiler runs with non-default LLVM optimization level 2 in production paths, risking honest-node divergence from CASM execution - (File: crates/apollo_compile_to_native_types/src/lib.rs)

### Summary
The sequencer's Cairo Native compilation pipeline (used by the gateway and batcher to execute declared Cairo 1 contracts faster than the CASM interpreter) defaults to LLVM optimization level 2 rather than the safest/no-optimization level, and this setting is shipped as-is in the production deployment configs. This is directly analogous to the reported bug class: enabling non-default, less battle-tested compiler optimizations on a path that must produce bit-for-bit identical results to the canonical (CASM) execution path, without a runtime enforced equivalence check — only ad-hoc unit tests and an optional offline replay tool exist to catch divergences.

### Finding Description
`SierraCompilationConfig::default()` in `apollo_compile_to_native_types/src/lib.rs` sets `optimization_level: DEFAULT_OPTIMIZATION_LEVEL` where `DEFAULT_OPTIMIZATION_LEVEL: u8 = 2` [1](#0-0) . This value is passed straight to the `cairo-native` compiler binary as `--opt-level` in `SierraToNativeCompiler::compile` [2](#0-1) .

Production deployment configs for both the `gateway` and `batcher` components explicitly ship `optimization_level: 2` and enable `cairo_native_mode: "lazy_compilation"`, meaning Native execution is active on real transaction-reachable paths (declare-triggered compilation, subsequent invoke-triggered native execution) rather than being an opt-in test-only feature: [3](#0-2) [4](#0-3) .

Once compiled, `RunnableCompiledClass::V1Native` is dispatched to `native_entry_point_execution::execute_entry_point_call` instead of the CASM interpreter whenever the tracked resource is Sierra gas [5](#0-4) . The sequencer's correctness model implicitly assumes that Native (LLVM `-O2`-optimized machine code) and CASM (Cairo VM bytecode) execution of the *same* Sierra program always produce identical state diffs, fees, retdata, events, and revert strings — this is exactly the "audited, default" vs. "optional, optimized" divergence risk flagged in the referenced Solidity-optimizer report. The safety net for this assumption is:
- A handful of unit tests asserting native/CASM output equality for specific syscalls (`test_builtin_counts_consistency`, `test_revert_text_is_backend_invariant_for_sierra_gas`, `positive_flow` in emit_event/keccak/library_call/secp) [6](#0-5) [7](#0-6) .
- An optional, manually-invoked `blockifier_reexecution` "compare-native" tool that re-executes historical mainnet blocks twice and diffs the state diffs — this is not run automatically as part of block production or as a consensus safety check [8](#0-7) [9](#0-8) .

None of these constitute exhaustive coverage guaranteeing that LLVM `-O2` codegen for every possible Sierra program compiles to code semantically identical to the CASM/Cairo-VM interpretation, particularly for arithmetic edge cases, overflow/UB-adjacent Sierra libfuncs, or newly introduced libfuncs where native lowering is less mature than the CASM lowering. Just like the Solidity report notes that "optional optimizations... are not as battle-tested as the default optimizations," enabling `-O2` codegen for Cairo Native without complete cross-backend behavioral test coverage carries an analogous risk in this sequencer.

### Impact Explanation
If a divergence between Native and CASM execution occurs on any transaction (invoke, declare, or L1 handler) that triggers native execution, nodes running with Native enabled (per current deployment configs) would compute a different state diff, fee, or revert reason than nodes/replicas relying on CASM execution for the same transaction and state. This directly causes honest-node divergence: different committed state roots / block hashes for the same block, which can halt consensus or create a chain split — one of the explicitly accepted impact categories.

### Likelihood Explanation
This is reachable by any ordinary user: declaring and then invoking a Cairo 1 contract is a standard, permissionless action, and Native execution is already the configured default in the shipped `gateway_config.json` / `batcher_config.json` app configs (`lazy_compilation` mode, `optimization_level: 2`). The likelihood is bounded by the (unknown but nonzero) probability of a codegen bug in the `cairo-native`/LLVM `-O2` path for some class of Sierra programs, since there is no exhaustive proof of equivalence, only spot-check tests and an optional offline comparison tool.

### Recommendation
- Run Cairo Native compilation at the safest known optimization level (e.g., `OptLevel::None` / level 0) in production until full behavioral-equivalence test coverage between Native and CASM exists for every supported libfunc and edge case, mirroring the "reduce risk before enabling optimizations" recommendation in the referenced report.
- Promote the existing `compare-native` re-execution mode from an optional/offline tool into a mandatory, automated part of CI/staging validation for every new `cairo-native` version and Sierra libfunc addition.
- Consider adding a runtime safety net (e.g., periodic or sampled dual-execution with alerting) rather than relying solely on manual replay jobs, given that the current design decouples Native and CASM correctness on live transaction-reachable paths.

### Proof of Concept
Not applicable — this is a design/configuration weakness (insufficiently-audited compiler optimization enabled by default on a consensus-critical execution path) rather than a single reproducible transaction exploit; a concrete instance would require finding a specific Sierra program on which `cairo-native -O2` and the CASM interpreter disagree, which is exactly the class of bug this analog warns is under-tested.

### Citations

**File:** crates/apollo_compile_to_native_types/src/lib.rs (L11-40)
```rust
// TODO(Noa): Reconsider the default values.
pub const DEFAULT_MAX_FILE_SIZE: u64 = 50 * 1024 * 1024;
pub const DEFAULT_MAX_CPU_TIME: u64 = 600;
pub const DEFAULT_MAX_MEMORY_USAGE: u64 = 15 * 1024 * 1024 * 1024;
pub const DEFAULT_OPTIMIZATION_LEVEL: u8 = 2;

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

impl Default for SierraCompilationConfig {
    fn default() -> Self {
        Self {
            compiler_binary_path: None,
            max_file_size: Some(DEFAULT_MAX_FILE_SIZE),
            max_cpu_time: DEFAULT_MAX_CPU_TIME,
            max_memory_usage: DEFAULT_MAX_MEMORY_USAGE,
            optimization_level: DEFAULT_OPTIMIZATION_LEVEL,
        }
    }
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

**File:** crates/apollo_deployments/resources/app_configs/batcher_config.json (L33-42)
```json
  "batcher_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode": "lazy_compilation",
  "batcher_config.static_config.contract_class_manager_config.cairo_native_run_config.channel_size": 2000,
  "batcher_config.static_config.contract_class_manager_config.cairo_native_run_config.panic_on_compilation_failure": false,
  "batcher_config.static_config.contract_class_manager_config.contract_cache_size": 2000,
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.compiler_binary_path": "",
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.compiler_binary_path.#is_none": true,
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.max_file_size": 52428800,
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.max_file_size.#is_none": false,
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.max_memory_usage": 16106127360,
  "batcher_config.static_config.contract_class_manager_config.native_compiler_config.optimization_level": 2,
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L5-14)
```json
  "gateway_config.static_config.contract_class_manager_config.cairo_native_run_config.channel_size": 2000,
  "gateway_config.static_config.contract_class_manager_config.cairo_native_run_config.panic_on_compilation_failure": false,
  "gateway_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode": "lazy_compilation",
  "gateway_config.static_config.contract_class_manager_config.contract_cache_size": 300,
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.compiler_binary_path": "",
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.compiler_binary_path.#is_none": true,
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.max_file_size": 52428800,
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.max_file_size.#is_none": false,
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.max_memory_usage": 16106127360,
  "gateway_config.static_config.contract_class_manager_config.native_compiler_config.optimization_level": 2,
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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/builtins_test.rs (L94-149)
```rust
#[test]
#[cfg(feature = "cairo_native")]
fn test_builtin_counts_consistency() {
    let test_contract_casm =
        FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm));
    let chain_info = &ChainInfo::create_for_testing();
    let mut casm_state = test_state(chain_info, BALANCE, &[(test_contract_casm, 1)]);

    let entry_point_call_casm = CallEntryPoint {
        entry_point_selector: selector_from_name("test_builtin_counts_consistency"),
        calldata: calldata![],
        ..trivial_external_entry_point_new(test_contract_casm)
    };

    let casm_call_info = entry_point_call_casm.execute_directly(&mut casm_state).unwrap();
    assert!(!casm_call_info.execution.failed, "CASM execution failed, {casm_call_info:?}");

    let expected_builtins = [
        BuiltinName::range_check,
        BuiltinName::pedersen,
        BuiltinName::poseidon,
        BuiltinName::keccak,
        BuiltinName::bitwise,
        BuiltinName::ec_op,
        BuiltinName::add_mod,
        BuiltinName::mul_mod,
        BuiltinName::range_check96,
    ];
    // Check that all builtins are covered by this test.
    for builtin in expected_builtins {
        assert!(
            casm_call_info.builtin_counters.get(&builtin.into()).copied().unwrap_or(0) > 0,
            "Builtin {builtin:?} was not called"
        );
    }

    let test_contract_native =
        FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Native));
    let mut native_state = test_state(chain_info, BALANCE, &[(test_contract_native, 1)]);

    let entry_point_call_native = CallEntryPoint {
        entry_point_selector: selector_from_name("test_builtin_counts_consistency"),
        calldata: calldata![],
        ..trivial_external_entry_point_new(test_contract_native)
    };

    let native_call_info = entry_point_call_native.execute_directly(&mut native_state).unwrap();
    assert!(!native_call_info.execution.failed, "Native execution failed");

    let casm_builtins = &casm_call_info.builtin_counters;
    let native_builtins = &native_call_info.builtin_counters;
    assert_eq!(
        casm_builtins, native_builtins,
        "Builtin usage should be identical between CASM and Native"
    );
}
```

**File:** crates/blockifier/src/execution/stack_trace_test.rs (L891-918)
```rust
/// At v0.14.3+ (strip policy on), the same Cairo 1 flow must produce a byte-identical revert
/// string regardless of execution backend — this is what makes `receipt_commitment` invariant
/// under cairo-native vs cairo-vm CASM. Pre-patch (origin/main-v0.14.3), this assertion would
/// fail: CASM emitted `Error at pc=0:443:` / `Error at pc=0:797:` lines under the outer two
/// frames that native never produced, see the historical diff of
/// `test_contract_ctor_frame_stack_trace_cairo1_casm.txt`.
#[cfg(feature = "cairo_native")]
#[rstest]
fn test_revert_text_is_backend_invariant_for_sierra_gas(
    block_context: BlockContext,
    default_all_resource_bounds: ValidResourceBounds,
) {
    let casm_revert = render_faulty_ctor_revert(
        &block_context,
        default_all_resource_bounds,
        RunnableCairo1::Casm,
    );
    let native_revert = render_faulty_ctor_revert(
        &block_context,
        default_all_resource_bounds,
        RunnableCairo1::Native,
    );
    assert_eq!(
        casm_revert, native_revert,
        "Cairo 1 revert text must be backend-invariant at v0.14.3+; CASM and Native \
         diverged.\nCASM:\n{casm_revert}\n\nNative:\n{native_revert}"
    );
}
```

**File:** crates/blockifier_reexecution/src/rpc_replay.rs (L243-295)
```rust
/// Reexecutes a single block twice -- once with native and once with CASM -- and compares the
/// resulting state diffs and transaction hashing data against each other.
///
/// Comparing transaction hashing data (execution outputs, signatures) is equivalent to comparing
/// block hashes without the overhead of computing commitments.
#[cfg(feature = "cairo_native")]
fn reexecute_block_native_vs_casm(
    block_number: u64,
    config: &RpcStateReaderConfig,
    chain_info: &ChainInfo,
    native_manager: &ContractClassManager,
    casm_manager: &ContractClassManager,
    prefetch_initial_reads: bool,
) -> ReexecutionResult<bool> {
    let min_sierra_version_override = Some(SierraVersion::new(0, 0, 0));

    let mut native_readers = RpcBlockReexecutor::new(
        BlockNumber(block_number),
        Some(config.clone()),
        chain_info.clone(),
        false,
        native_manager.clone(),
        prefetch_initial_reads,
    );
    native_readers.min_sierra_version_override = min_sierra_version_override.clone();
    let ReexecuteBlockOutcome {
        actual_state_diff: native_state_diff,
        txs_hashing_data: native_txs_hashing_data,
        ..
    } = native_readers.reexecute_block()?;

    let mut casm_readers = RpcBlockReexecutor::new(
        BlockNumber(block_number),
        Some(config.clone()),
        chain_info.clone(),
        false,
        casm_manager.clone(),
        prefetch_initial_reads,
    );
    casm_readers.min_sierra_version_override = min_sierra_version_override;
    let ReexecuteBlockOutcome {
        actual_state_diff: casm_state_diff,
        txs_hashing_data: casm_txs_hashing_data,
        ..
    } = casm_readers.reexecute_block()?;

    let state_diff_matched =
        compare_state_diffs(native_state_diff, casm_state_diff, BlockNumber(block_number));
    let tx_hashing_data_matched =
        compare_tx_hashing_data(native_txs_hashing_data, casm_txs_hashing_data, block_number);

    Ok(state_diff_matched && tx_hashing_data_matched)
}
```

**File:** crates/blockifier_reexecution/replay/README.md (L1-10)
```markdown
# RPC Replay

Continuously reexecutes blocks fetched via RPC and compares the resulting state
diffs to verify correctness. Supports two modes:

- **Standard** (default): reexecutes each block once and compares the actual
  state diff against the expected one from the chain.
- **Compare-native** (`--compare-native`): reexecutes each block twice — once
  with Cairo Native and once with CASM — and compares the two state diffs
  against each other. Requires the `cairo_native` feature.
```
