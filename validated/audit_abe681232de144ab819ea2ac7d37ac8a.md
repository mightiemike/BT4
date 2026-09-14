### Title
Contract-loading gas fee ignores wasm module structure, allowing cheap FunctionCall receipts to trigger disproportionate per-call CPU work (instantiation-cost DoS) - ([File: runtime/near-vm-runner/src/logic/gas_counter.rs])

### Summary
The gas fee charged for loading/instantiating a contract on every `FunctionCall` is computed purely from the wasm code's byte length, not from the number of globals, active data segments, or active element (table) segments the module declares. Because Wasmtime must reinitialize all of these structures on every instantiation (i.e., every `FunctionCall` receipt execution), a contract crafted with a compact byte encoding but a very large count of globals/data-segments/element-segments lets an unprivileged signer pay for a small amount of gas while making validators perform disproportionate CPU work on each call — an uncontrolled per-call resource-consumption pattern, analogous in bug-class to CVE-2021-33135 (uncontrolled resource consumption enabling DoS), reachable purely through ordinary deployed-contract function calls.

### Finding Description
`add_contract_loading_fee` explicitly documents that the loading fee is **length-only**, not structure-aware: [1](#0-0) 

This fee (`contract_loading_base + contract_loading_bytes * code_len`) is the *only* per-call charge tied to loading/instantiating the compiled contract, and it is charged on every `FunctionCall` action via `before_loading_executable`/`after_loading_executable`, gated by `fix_contract_loading_cost`: [2](#0-1) 

The runtime-params-estimator itself documents this gap as a known, unmitigated adversarial cost class — three dedicated "adversarial" cost estimators exist specifically to measure it: [3](#0-2) 

and the measurement helpers construct contracts with tens of thousands of globals/data-segments/element-segments and measure the *actual* per-invocation instantiation overhead, confirming it is real, non-trivial, per-call CPU cost: [4](#0-3) 

A regression test independently confirms that a contract with 100,000 declared globals (well within the protocol's `max_globals_per_contract` limit, which defaults to 100,000) meaningfully inflates the compiled/instantiated core-instance footprint (~1.6 MB of globals vs. the 1 MiB pooling-allocator slot), and that this failure mode is only caught as a load-time backstop, not priced by gas ahead of time: [5](#0-4) 

Since the estimator uses only 50,000 globals / 50,000 data segments / 10,000 element segments (safely under the 100k/1-table/10M-element limits documented in `Preparation.md`), such a contract will load and run successfully on every call — the "TooManyGlobals"/"TooManyTableElements" checks in `prepare_v3.rs` only bound the counts at generous protocol maxima, they do not translate the counts into extra per-call gas. [6](#0-5) 

### Impact Explanation
An attacker can deploy one contract (paying a normal one-time deploy fee, itself bounded by `max_deploy_actions_per_receipt`/compute limits) containing tens of thousands of globals and/or active data/element segments, encoded compactly so the wasm byte length — and hence the loading gas fee — stays small. Every subsequent cheap `FunctionCall` to this contract then forces validators to redo the disproportionate globals/table/memory-segment initialization work on the hot path of chunk application, while the signer only pays the gas commensurate with a small contract. Because a chunk can pack many such calls up to the gas limit, and gas no longer accurately reflects CPU time for these calls, an attacker can inflate real per-chunk execution wall-time far beyond what the gas budget implies, without any protocol violation being flagged. This is a resource-consumption/DoS class impact: it can slow or stall chunk production (a shared, node-wide effect), which is the acceptable "transaction-triggered halt"-class impact for this scan.

### Likelihood Explanation
Reaching this path only requires deploying an ordinary, protocol-valid contract (via `DeployContractAction`, subject to normal validation and existing count limits) and then submitting ordinary `FunctionCall` transactions — fully within reach of any unprivileged transaction signer / contract deployer, with no special permission or validator/network position required. The building blocks (large global/data/element counts within existing limits, cheap-looking function bodies) are demonstrated to be constructible by the very estimator code shipped in this repository.

### Recommendation
Introduce a gas cost component for contract *structure* at either deploy-time (encoded into per-call loading fee, similar to `contract_loading_bytes`) or at instantiation-time, proportional to the number of globals, active data segments, and active/element-table entries the module declares — mirroring the linear per-byte model already used for `contract_loading_bytes`, but keyed to the actual per-call reinitialization cost rather than to raw code length. Alternatively, tighten `max_globals_per_contract`, active data-segment counts, and `max_elements_per_contract_table` to bounds where the additional CPU cost is provably negligible relative to `wasm_contract_loading_base`. The runtime-params-estimator's existing `AdversarialLoadManyGlobals`/`AdversarialLoadManyDataSegments`/`AdversarialLoadManyElementSegments` measurements should be wired into the actual `RuntimeConfig` fee derivation rather than remaining diagnostic-only.

### Proof of Concept
Not independently executed in this ask-only session; however, the codebase's own estimator scaffolding provides a ready reproduction recipe:
1. Build a contract via `near_test_contracts::contract_with_num_globals(50_000)` (or the equivalent `many_data_segments_contract(50_000)` / `many_element_segments_contract(10_000)` helpers) — all within protocol limits.
2. Deploy it once via `DeployContractAction`.
3. Repeatedly invoke a trivial exported method (e.g., `main`) via `FunctionCall` actions.
4. Compare gas charged (dominated by `contract_loading_base + contract_loading_bytes * len(code)`, small since the compact wasm encoding of many globals/segments is tiny) against actual measured wall-clock/instantiation cost, as already done by `measure_instantiation_overhead` in `runtime/runtime-params-estimator/src/vm_estimator.rs:186-224`, to quantify the gas/CPU mismatch on the target runtime configuration. [7](#0-6)

### Citations

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L216-227)
```rust
    /// Add a cost for loading the contract code in the VM.
    ///
    /// This cost does not consider the structure of the contract code, only the
    /// size. This is currently the only loading fee. A fee that takes the code
    /// structure into consideration could be added. But since that would have
    /// to happen after loading, we cannot pre-charge it. This is the main
    /// motivation to (only) have this simple fee.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn add_contract_loading_fee(&mut self, code_len: u64) -> Result<()> {
        self.pay_per(ExtCosts::contract_loading_bytes, code_len)?;
        self.pay_base(ExtCosts::contract_loading_base)
    }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L229-256)
```rust
    /// VM independent setup before loading the executable.
    ///
    /// Does VM independent checks that happen after the host state has been set
    /// up but before loading the executable. This includes pre-charging gas
    /// costs for loading the executable, which depends on the size of the WASM code.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn before_loading_executable(
        &mut self,
        config: &near_parameters::vm::Config,
        method_name: &str,
        wasm_code_bytes: u64,
    ) -> std::result::Result<(), super::errors::FunctionCallError> {
        if method_name.is_empty() {
            let error = super::errors::FunctionCallError::MethodResolveError(
                super::errors::MethodResolveError::MethodEmptyName,
            );
            return Err(error);
        }
        if config.fix_contract_loading_cost {
            if self.add_contract_loading_fee(wasm_code_bytes).is_err() {
                let error =
                    super::errors::FunctionCallError::HostError(super::HostError::GasExceeded);
                return Err(error);
            }
        }
        Ok(())
    }

```

**File:** runtime/runtime-params-estimator/src/cost.rs (L747-756)
```rust
    AdversarialCompileMaxBlocks,
    /// Invocation cost with 100k zero-initialized globals.
    /// Exposes unbounded per-call Wasmtime global re-initialization not covered by gas.
    AdversarialLoadManyGlobals,
    /// Invocation cost with 50k active data segments.
    /// Exposes unbounded per-call data-segment initialization not covered by gas.
    AdversarialLoadManyDataSegments,
    /// Invocation cost with 10k active element segments.
    /// Exposes unbounded per-call table-initialization work not covered by gas.
    AdversarialLoadManyElementSegments,
```

**File:** runtime/runtime-params-estimator/src/vm_estimator.rs (L168-224)
```rust
pub(crate) fn adversarial_load_many_globals(metric: GasMetric, vm_kind: VMKind) -> GasCost {
    let code = near_test_contracts::contract_with_num_globals(50_000);
    measure_instantiation_overhead(metric, vm_kind, &code)
}

pub(crate) fn adversarial_load_many_data_segments(metric: GasMetric, vm_kind: VMKind) -> GasCost {
    let code = near_test_contracts::many_data_segments_contract(50_000);
    measure_instantiation_overhead(metric, vm_kind, &code)
}

pub(crate) fn adversarial_load_many_element_segments(
    metric: GasMetric,
    vm_kind: VMKind,
) -> GasCost {
    let code = near_test_contracts::many_element_segments_contract(10_000);
    measure_instantiation_overhead(metric, vm_kind, &code)
}

/// Warm the compile cache, then measure N invocations (instantiation + trivial execution).
/// The function body is a bare `end`, so execution cost is negligible.
fn measure_instantiation_overhead(
    metric: GasMetric,
    vm_kind: VMKind,
    contract_bytes: &[u8],
) -> GasCost {
    let config_store = RuntimeConfigStore::new(None);
    let mut config = config_store.get_config(PROTOCOL_VERSION).wasm_config.as_ref().clone();
    config.vm_kind = vm_kind;
    let config = Arc::new(config);
    let fees = Arc::new(RuntimeFeesConfig::test());
    let code = ContractCode::new(contract_bytes.to_vec(), None);
    let cache = MockContractRuntimeCache::default();
    let mut fake_external = near_vm_runner::logic::mocks::mock_external::MockedExternal::with_code(
        code.clone_for_tests(),
    );

    let mut run_once = || {
        let context = create_context(vec![]);
        let gas_counter = context.make_gas_counter(&config);
        vm_kind
            .runtime(config.clone())
            .unwrap()
            .prepare(&fake_external, Some(&cache), gas_counter, "main")
            .run(&mut fake_external, &context, Arc::clone(&fees))
            .expect("fatal_error")
    };

    // Warm: compiles and caches the module; subsequent calls only instantiate + execute.
    run_once();

    let n = 10_usize;
    let start = GasCost::measure(metric);
    for _ in 0..n {
        run_once();
    }
    start.elapsed() / n as u64
}
```

**File:** runtime/near-vm-runner/src/tests/runtime_errors.rs (L14-29)
```rust
/// Compile and load a contract with 100k globals.
///
/// Each defined global occupies 16 bytes of a core instance's `VMContext`, so
/// globals alone add ~1.6 MB. That breaches the 1MiB `max_core_instance_size`
/// slot of the Wasmtime pooling allocator, so loading the module at
/// `Module::deserialize` fails.
///
/// With `max_globals_per_contract` set to 100k this contract still passes
/// `prepare` and is caught by the Wasmtime backstop at load time.
///
/// Pre-`FixContractLoadingError` this surfaces as `VMRunnerError::LoadingError`,
/// which the runtime maps to a zero-gas nop — the contract-loading work is left
/// uncharged. Post-feature the same failure finalizes as a gas-bearing abort
/// that charges the contract-loading fee. Either way it must not panic / crash
/// the node.
#[test]
```

**File:** docs/RuntimeSpec/Preparation.md (L43-57)
```markdown
* The wasm module may contain no more than:
  * `1_000_000` distinct signatures;
  * `1_000_000` function imports and local function definitions;
  * `100_000` imports;
  * `100_000` exports;
  * `1_000_000` global imports and module-local global definitions;
  * `100_000` data segments;
  * `1` table;
  * `1` memory;
* UTF-8 strings comprising the wasm module definition (e.g. export name) may not exceed `100_000`
  bytes each;
* Function definitions may not specify more than `50_000` locals;
* Signatures may not specify more than `1_000` parameters;
* Signatures may not specify more than `1_000` results;
* Tables may not specify more than `10_000_000` entries;
```
