### Title
Uncharged per-call WASM instantiation cost from globals/data-segments/element-segments enables gas-underpriced resource-exhaustion DoS - (File: `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`, `runtime/runtime-params-estimator/src/vm_estimator.rs`)

### Summary
Every `FunctionCall` re-instantiates the deployed WASM module through Wasmtime before executing the entry point. That instantiation step re-initializes all module globals, active data segments, and active element segments. The codebase's own cost estimators document that this per-call work is **not covered by any dedicated gas fee** — the only fee charged for loading/instantiation is `contract_loading_base` + `contract_loading_bytes * code_len`, a fee that is linear purely in the *raw byte size* of the WASM binary, not in the structural cost of re-initializing potentially tens of thousands of globals/segments on every single call.

### Finding Description
`WasmtimeVM::with_compiled_and_loaded` charges the contract-loading fee based only on `wasm_bytes` (code length): [1](#0-0) 

This fee is pre-charged (or post-charged, depending on protocol feature) purely as a function of code length, with no accounting for the number of globals, active data segments, or active element segments that Wasmtime must re-materialize on every instantiation: [2](#0-1) 

The engineering team itself explicitly flags this gap via dedicated adversarial cost-estimator entries: [3](#0-2) 

And the corresponding benchmark harness deliberately measures *per-call instantiation overhead* (not one-time compile cost) for contracts containing many globals / data segments / element segments, explicitly noting the work happens every call: [4](#0-3) 

Helper contract generators exist specifically to construct such adversarial contracts (e.g. 50k globals, 50k data segments, 10k element segments), confirming this is a known, reproducible structural pattern: [5](#0-4) 

The module-shape limits enforced during `prepare` (`max_globals_per_contract`, `max_functions_number_per_contract`, wasmparser's default data-segment/type limits, etc.) bound the *encoding size* of these constructs but do not bound — nor does any `ExtCosts` fee price — the *repeated per-invocation instantiation cost* they impose on every validator that re-executes the receipt: [6](#0-5) [7](#0-6) 

Because globals and small active-data/element segments can be encoded very compactly (a handful of bytes each in the WASM binary) while requiring the VM to write/initialize a corresponding memory or table slot on every single call, the byte-size-based `contract_loading_bytes` fee can substantially undercharge the true CPU cost of repeated invocation, exactly mirroring the underlying bug class in CVE-2021-37865: a compactly-encoded, cheaply-admitted payload whose processing cost is disproportionate to what is charged/validated for it.

### Impact Explanation
Any account that can submit a `DeployContractAction` followed by repeated `FunctionCallAction`s (an ordinary, unprivileged transaction signer) can deploy a module packed with the maximum permitted number of globals/data-segments/element-segments and then invoke it cheaply and repeatedly. Because the loading/instantiation fee is priced off code byte-length rather than the actual re-initialization work, an attacker can force every validator processing the corresponding chunk/receipt to repeatedly perform disproportionately expensive instantiation work for gas that does not reflect this cost. At scale (many receipts in a chunk, or sustained call volume) this degrades block/chunk production performance across all honest validators simultaneously — a transaction-triggered resource-exhaustion condition analogous to the Mattermost GIF-processing DoS.

### Likelihood Explanation
Reachable via a single `DeployContractAction` plus ordinary `FunctionCallAction`s — no special privileges, validator role, or network position needed. The contract shapes required (many globals/data-segments/element-segments) are explicitly known and reproducible in-repo via the `near-test-contracts` adversarial generators, and remain within currently configured per-module limits (`max_globals_per_contract: 100_000`, wasmparser default segment caps), so no protocol-limit bypass is required to construct the payload.

### Recommendation
Introduce a structural loading/instantiation fee (a new `ExtCosts`, e.g. `contract_loading_globals`, `contract_loading_data_segments`, `contract_loading_element_segments`) charged per-call proportional to the number of globals, active data segments, and active element segments in the module, in addition to the existing byte-length-based `contract_loading_bytes`/`contract_loading_base` fee. Use the existing `AdversarialLoadManyGlobals`/`AdversarialLoadManyDataSegments`/`AdversarialLoadManyElementSegments` estimator measurements to calibrate these new fees so instantiation cost is charged proportionally to actual work, not merely to raw code size.

### Proof of Concept
1. Deploy a contract generated by `near_test_contracts::contract_with_num_globals(100_000)` (or the equivalent max permitted count), or `many_data_segments_contract` / `many_element_segments_contract` at their respective limits — all valid under current `prepare` limits.
2. Call the contract's trivial exported `main` function repeatedly via ordinary `FunctionCallAction`s.
3. Each call is charged only `contract_loading_base + contract_loading_bytes * code_len` (small, since the module encodes compactly) yet forces Wasmtime to re-initialize the full set of globals/data-segments/element-segments on every call — the exact discrepancy the `Adversarial*` estimator functions in `runtime/runtime-params-estimator/src/vm_estimator.rs:168-223` were built to quantify.
4. Repeating this call cheaply across many receipts imposes disproportionate CPU cost on every validator relative to gas paid, producing a resource-exhaustion DoS analogous to CVE-2021-37865's crafted-file processing cost mismatch.

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

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L229-255)
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

**File:** runtime/runtime-params-estimator/src/cost.rs (L748-756)
```rust
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

**File:** runtime/runtime-params-estimator/src/vm_estimator.rs (L168-223)
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
```

**File:** runtime/near-test-contracts/src/lib.rs (L252-350)
```rust
/// Many zero-initialized globals.
pub fn contract_with_num_globals(num_globals: u32) -> Vec<u8> {
    let mut module = Module::new();
    let mut types = TypeSection::new();
    types.ty().function([], []);
    module.section(&types);
    let mut funcs = FunctionSection::new();
    funcs.function(0);
    module.section(&funcs);
    let mut globals = GlobalSection::new();
    for _ in 0..num_globals {
        globals.global(
            GlobalType { val_type: ValType::I32, mutable: false, shared: false },
            &ConstExpr::i32_const(0),
        );
    }
    module.section(&globals);
    let mut exports = ExportSection::new();
    exports.export("main", ExportKind::Func, 0);
    module.section(&exports);
    let mut code = CodeSection::new();
    let mut f = Function::new([]);
    f.instruction(&Instruction::End);
    code.function(&f);
    module.section(&code);
    module.finish()
}

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

/// Many active element segments, each writing function 0 into table slot 0.
pub fn many_element_segments_contract(num_segments: u32) -> Vec<u8> {
    let mut module = Module::new();
    let mut types = TypeSection::new();
    types.ty().function([], []);
    module.section(&types);
    let mut funcs = FunctionSection::new();
    funcs.function(0);
    module.section(&funcs);
    let mut tables = TableSection::new();
    tables.table(TableType {
        element_type: RefType::FUNCREF,
        minimum: 1,
        maximum: Some(1),
        table64: false,
        shared: false,
    });
    module.section(&tables);
    let mut exports = ExportSection::new();
    exports.export("main", ExportKind::Func, 0);
    module.section(&exports);
    let mut elements = ElementSection::new();
    for _ in 0..num_segments {
        elements.active(
            Some(0),
            &ConstExpr::i32_const(0),
            Elements::Functions(Cow::Borrowed(&[0u32])),
        );
    }
    module.section(&elements);
    let mut code = CodeSection::new();
    let mut f = Function::new([]);
    f.instruction(&Instruction::End);
    code.function(&f);
    module.section(&code);
    module.finish()
}
```

**File:** docs/RuntimeSpec/Preparation.md (L37-57)
```markdown
A number of limits are imposed on the WebAssembly module that is being parsed:

* The length of the wasm code must not exceed `max_contract_size` genesis configuration parameter;
* The wasm module must be a valid module according to the WebAssembly core 1.0 specification (this
  means no extensions such as multi value returns or SIMD; this limitation may be relaxed in the
  future).
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

**File:** core/parameters/res/runtime_configs/157.yaml (L1-1)
```yaml
max_globals_per_contract: { new: 100_000 }
```
