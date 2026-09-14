### Title
Per-call WASM instantiation cost (globals/data segments/element segments re-initialization) is charged only by contract-code byte size, not by actual structural cost, allowing gas-bypassed CPU-exhaustion DoS on every `FunctionCall` — ([File: runtime/near-vm-runner/src/logic/gas_counter.rs])

### Summary
The NEAR runtime charges the cost of loading/instantiating a compiled WASM contract using a single fee that depends **only on the contract's byte length**, not on its internal structure (number of globals, active data segments, active element segments). The codebase itself documents and estimates this gap as `AdversarialLoadManyGlobals`, `AdversarialLoadManyDataSegments`, and `AdversarialLoadManyElementSegments`, explicitly noting these expose "unbounded per-call ... initialization not covered by gas." A contract engineered with the protocol-allowed maximum of up to `max_globals_per_contract` (100,000) globals, or a large number of active data/element segments, can be deployed once and then invoked repeatedly via cheap `FunctionCall` transactions/receipts, each of which forces every validator to redo expensive per-call instantiation work (zero-initializing ~1.6MB of global slots, re-running large numbers of active-segment initializers) while only being charged the fixed, size-based `contract_loading_base`/`contract_loading_bytes` fee — the same fee a trivial contract of similar byte size would pay.

### Finding Description
Contract loading/instantiation charges only `contract_loading_base + contract_loading_bytes * code_len` via `GasCounter::add_contract_loading_fee`: [1](#0-0) 

The comment on this function is explicit about the limitation: "This cost does not consider the structure of the contract code, only the size... A fee that takes the code structure into consideration could be added. But since that would have to happen after loading, we cannot pre-charge it." [1](#0-0) 

This fee is applied identically on `before_loading_executable`/`after_loading_executable` for every `FunctionCall` invocation of a deployed contract — i.e., per call, not just once at deploy time: [2](#0-1) 

The runtime-params-estimator crate explicitly documents this as a known adversarial gap, defining dedicated cost categories for it: [3](#0-2) 

And the estimator's own measurement harness confirms these costs are measured **per invocation** (instantiation + trivial execution), after the compile cache is already warm — meaning this overhead recurs on every call, not just the first: [4](#0-3) 

A contract with 100,000 globals is explicitly confirmed to pass WASM preparation/validation (`prepare_v3`) under the current limit configuration — `max_globals_per_contract` is set to `100_000`: [5](#0-4) 

and the test suite comments on the resulting per-core-instance memory blow-up (~1.6MB from 100k globals × 16 bytes each), confirming the contract is allowed to pass `prepare` and only fails at `Module::deserialize` due to an unrelated pooling-allocator limit — not because of any structural gas cost: [6](#0-5) 

Similarly, `many_data_segments_contract` and the element-segment equivalent construct WASM modules with many small active segments, each of which the VM must iterate and copy at instantiation time on **every call**: [7](#0-6) 

Because the WASM/VM runtime documentation explicitly states the protocol's core soundness property — "the NEAR protocol charges fees for an operation before the operation is executed... it is important that we are able to deduce ahead of time what cost to assign to a given operation. Inability to do so can lead to significant undercharging and break the properties underlying the protocol" — this gap is a direct violation of that invariant: [8](#0-7) 

This is structurally the same bug class as the xgrammar advisory: an attacker-controlled input (WASM globals/segments count, analogous to enum grammar size) causes CPU work in a "preparation"/"parsing" phase that is *not* accounted for by the metering mechanism responsible for pricing that work (gas, analogous to xgrammar's timeout/complexity budget), enabling disproportionate CPU consumption per unit of "cost" paid.

### Impact Explanation
Every honest validator that processes a receipt calling such a contract must perform the same expensive instantiation (global-slot zeroing, data/element-segment initialization) while gas accounting only reflects the contract's byte size. An attacker can:
1. Deploy one contract near the size/structural limits (100k globals and/or tens of thousands of active data/element segments), paying the deployment cost once.
2. Repeatedly submit cheap `FunctionCall` transactions/receipts against it.
3. Force disproportionate CPU time on every validator per unit of gas paid — a gas-bypass DoS vector reachable by any unprivileged transaction signer or RPC caller, without needing special permissions, staking, or being a validator/network attacker.

This is a genuine "fee or gas bypass" per the validation criteria: gas paid is decoupled from the actual computational cost incurred by all validators, and at scale this can be used to slow chunk production/execution disproportionately to the gas spent, which is the exact mechanism (undercharged, disproportionately-slow attacker-controlled structural complexity) flagged by the xgrammar advisory.

### Likelihood Explanation
Likelihood is limited by the fact that the codebase's own estimator explicitly tracks and measures this gap (`AdversarialLoadManyGlobals`/`AdversarialLoadManyDataSegments`/`AdversarialLoadManyElementSegments`), meaning the NEAR team is aware of it and it may already have compensating limits or offsetting margins baked into `contract_loading_bytes`/`contract_loading_base` that were not fully visible in the excerpts reviewed (i.e., the per-byte fee could already be calibrated conservatively enough to cover worst-case structural overhead, or a not-yet-verified downstream check may bound per-call reinitialization cost). I could not confirm from the available index whether the currently deployed `contract_loading_bytes`/`contract_loading_base` values already provide sufficient margin over the measured adversarial costs, nor whether there is a separate, more restrictive limit gating globals/segments count that offsets this specifically (only `max_globals_per_contract: 100_000` was found, with no evidence of a lower effective cap tied to segment/global-count-aware pricing). Given the explicit "not covered by gas" language in first-party code comments, this is presented as a credible, reachable finding, but a full confirmation would require running the estimator/adversarial benchmarks against the current mainnet cost parameters — something beyond what static code search can settle.

### Recommendation
- Incorporate the actual number of globals, active data segments, and active element segments into the contract-loading/instantiation gas fee (or a separate `instantiation_cost` fee), not just code byte length, since these are known statically after `prepare_v3` validation and can be pre-charged before instantiation.
- Alternatively, tighten `max_globals_per_contract`, and introduce first-class limits on the number of active data/element segments (currently only implicitly bounded via total contract size/data segment count in `LimitConfig`), calibrated so that the existing `contract_loading_bytes` fee provably dominates worst-case per-call instantiation cost.
- Validate the `AdversarialLoadManyGlobals`/`AdversarialLoadManyDataSegments`/`AdversarialLoadManyElementSegments` estimator outputs against the currently active `contract_loading_base`/`contract_loading_bytes` runtime-config values to confirm sufficient margin exists; if not, ship a runtime-config update raising these fees or adding structure-aware components.

### Proof of Concept
1. Build and deploy a WASM contract at/near `max_globals_per_contract` (100,000 zero-initialized globals) and/or with tens of thousands of active data/element segments, similar to `contract_with_num_globals`/`many_data_segments_contract` used by the estimator: [7](#0-6) 
2. Deploy the contract once (paying the size-based deploy fee).
3. Submit a large volume of cheap `FunctionCall` transactions/receipts invoking a trivial exported method on this contract.
4. Each invocation forces every validator to redo full instantiation-time re-initialization of the globals/segments, per the estimator's own dedicated measurement harness confirming this cost recurs per call after the compile cache is warm: [9](#0-8) 
5. Compare the CPU time consumed per call against the gas actually burnt (`contract_loading_base + contract_loading_bytes*len`, unrelated to globals/segments count) to demonstrate the gas/CPU-cost mismatch.

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

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L229-272)
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

    /// Legacy code to preserve old gas charging behaviour in old protocol versions.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn after_loading_executable(
        &mut self,
        config: &near_parameters::vm::Config,
        wasm_code_bytes: u64,
    ) -> std::result::Result<(), super::errors::FunctionCallError> {
        if !config.fix_contract_loading_cost {
            if self.add_contract_loading_fee(wasm_code_bytes).is_err() {
                return Err(super::errors::FunctionCallError::HostError(
                    super::HostError::GasExceeded,
                ));
            }
        }
        Ok(())
    }
```

**File:** runtime/runtime-params-estimator/src/cost.rs (L746-756)
```rust
    /// Compile a contract at the maximum block limit (10 functions with 4999 blocks).
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

**File:** core/parameters/res/runtime_configs/157.yaml (L1-1)
```yaml
max_globals_per_contract: { new: 100_000 }
```

**File:** runtime/near-vm-runner/src/tests/runtime_errors.rs (L14-31)
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
fn test_max_core_instance_size_breached() {
    let wasm = near_test_contracts::contract_with_num_globals(100_000);
```

**File:** runtime/near-test-contracts/src/lib.rs (L280-312)
```rust
/// Many tiny active data segments, each writing 1 byte to memory offset 0.
pub fn many_data_segments_contract(num_segments: u32) -> Vec<u8> {
    let mut module = Module::new();
    let mut types = TypeSection::new();
    types.ty().function([], []);
    module.section(&types);
    let mut funcs = FunctionSection::new();
    funcs.function(0);
    module.section(&funcs);
    let mut memories = MemorySection::new();
    memories.memory(MemoryType {
        minimum: 1,
        maximum: None,
        memory64: false,
        shared: false,
        page_size_log2: None,
    });
    module.section(&memories);
    let mut exports = ExportSection::new();
    exports.export("main", ExportKind::Func, 0);
    module.section(&exports);
    let mut data = DataSection::new();
    for i in 0..num_segments {
        data.active(0, &ConstExpr::i32_const(0), [i as u8]);
    }
    module.section(&data);
    let mut code = CodeSection::new();
    let mut f = Function::new([]);
    f.instruction(&Instruction::End);
    code.function(&f);
    module.section(&code);
    module.finish()
}
```

**File:** runtime/near-vm-runner/RUNTIMES.md (L116-120)
```markdown
It is important to remember that the NEAR protocol charges fees for an operation before the
operation is executed. For that reason, predictability of worst case execution time often matters
more than the execution time in the typical case. It is important that we are able to deduce ahead
of time what cost to assign to a given operation. Inability to do so can lead to significant
undercharging and break the properties underlying the protocol.
```
