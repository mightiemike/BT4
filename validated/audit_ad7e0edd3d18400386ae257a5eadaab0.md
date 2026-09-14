### Title
Unmetered WASM instantiation cost from many globals/data/element segments allows contract-deployer-triggered excessive CPU consumption per call - (File: runtime/near-vm-runner/src/prepare/prepare_v3.rs)

### Summary
The reported TIFF bug class is "a crafted, small-looking input causes a decoder to perform disproportionately large, unmetered work." The nearcore analog is WASM contract preparation/instantiation: `prepare_contract` in `runtime/near-vm-runner/src/prepare/prepare_v3.rs` enforces explicit limits on functions, locals, tables, table elements, types, and globals count individually [1](#0-0) , but the actual per-call instantiation cost of globals, data segments, and element segments (memory/table initialization work done by the underlying Wasmtime/NearVM runtime on every invocation) is not part of the gas-metered instruction stream, and is only tracked as an "adversarial" benchmark, not enforced as a hard runtime limit.

### Finding Description
`ElementSection` and `DataSection` payloads in `prepare_v3.rs` are passed through the `wasmparser` validator and copied verbatim into the output module without any nearcore-imposed count/size limit analogous to `type_limit`, `table_limit`, `global_limit`, or `function_limit` [2](#0-1) . The `GlobalSection` handler does subtract from `global_limit`, but `global_limit` defaults to `u64::MAX` when unset [3](#0-2)  and, more importantly, the *cost* of globals/data/element re-initialization at every function call is not charged as WASM ops — it is host-runtime instantiation overhead that happens before any gas-metered instruction executes.

This is corroborated by the runtime-params-estimator itself, which contains dedicated "adversarial" benchmarks explicitly documented as exposing *uncovered-by-gas* cost:
- `AdversarialLoadManyGlobals`: "Invocation cost with 100k zero-initialized globals. Exposes unbounded per-call Wasmtime global re-initialization not covered by gas." [4](#0-3) 
- `AdversarialLoadManyDataSegments`: "Invocation cost with 50k active data segments. Exposes unbounded per-call data-segment initialization not covered by gas." [5](#0-4) 
- `AdversarialLoadManyElementSegments`: "Invocation cost with 10k active element segments. Exposes unbounded per-call table-initialization work not covered by gas." [6](#0-5) 

The estimator functions `adversarial_load_many_globals`, `adversarial_load_many_data_segments`, and `adversarial_load_many_element_segments` build contracts with 50,000 globals / 50,000 data segments / 10,000 element segments respectively and measure per-call instantiation overhead separately from gas-metered execution [7](#0-6) , confirming the codebase's own authors recognize this as unmetered, "adversarial" CPU work — the exact TIFF-style bug class (compact encoding → disproportionate, unmetered decode/init cost).

This is directly analogous to the TIFF flaw: a WASM module with a tiny/compact encoded size (globals, data, and element sections can encode tens of thousands of entries in a relatively small binary, especially with LEB128-compact zero-initializers and short init-expressions) forces the runtime to perform O(n) initialization work on *every single invocation* (not just once at deploy/compile time), and this per-call cost is outside the `wasm_regular_op_cost`/`ExtCosts` gas-metering model documented in `docs/architecture/gas/README.md`, which states dynamic costs are charged per WASM instruction executed and per host-function base/byte cost, not per section-initialization entry [8](#0-7) .

### Impact Explanation
Any unprivileged account can deploy such a contract via a standard `DeployContract` action, then trigger it with a cheap `FunctionCall`. Because the disproportionate cost occurs during module instantiation — before the gas meter starts charging for guest instructions — a chunk producer executing this contract as part of normal transaction/receipt processing incurs CPU cost far exceeding what the attached/charged gas would suggest. If the per-call instantiation overhead is large enough relative to the chunk's gas/time budget, this can degrade or stall chunk production on the receiving shard for every honest validator that must apply the same receipt (a transaction-triggered slowdown/halt vector), since the underlying cost is deterministic and would affect all nodes identically.

### Likelihood Explanation
Reachable by any account with the ability to deploy and call a contract — the lowest possible privilege in the system. `prepare_v3.rs` does not reject large global/data/element counts by itself; a moderately sized WASM binary (well under `max_contract_size`) can pack tens of thousands of these entries via compact encodings, matching what the estimator's own test contracts (`contract_with_num_globals(50_000)`, `many_data_segments_contract(50_000)`, `many_element_segments_contract(10_000)`) demonstrate is achievable and measurable as a real per-call overhead.

### Recommendation
Enforce explicit nearcore-side limits on the number of active data segments, element segments, and global re-initializations analogous to the existing `type_limit`/`table_limit`/`function_limit` checks in `PrepareContext`, or ensure the estimated per-entry instantiation cost (`AdversarialLoadManyGlobals`/`DataSegments`/`ElementSegments`) is folded into a charged, per-call gas cost (e.g., a `wasm_contract_loading_base`/`per_byte`-style parameter that scales with segment/global counts) rather than left as an "adversarial" benchmark with no corresponding protocol-enforced cost or limit.

### Proof of Concept
1. Compile a WASM module containing 50,000 global declarations (or active data/element segments), each individually small (e.g., `i32` zero-initialized), keeping total binary size modest — mirroring `near_test_contracts::contract_with_num_globals(50_000)` / `many_data_segments_contract(50_000)` / `many_element_segments_contract(10_000)` used in `runtime/runtime-params-estimator/src/vm_estimator.rs`.
2. Deploy the module as a contract via a standard `DeployContract` action from an unprivileged account.
3. Submit repeated cheap `FunctionCall` transactions invoking any exported method (even a no-op).
4. Observe that per-call CPU time for instantiation (measured, as in `measure_instantiation_overhead`) scales with the segment/global count and is not reflected in gas burnt, since these estimator functions measure exactly this discrepancy as "not covered by gas" [9](#0-8) .

### Citations

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L42-48)
```rust
            function_limit: limits.max_functions_number_per_contract.unwrap_or(u64::MAX),
            local_limit: max_locals(code, config).unwrap_or(u64::MAX),
            function_body_size_limit: limits.max_function_body_size.unwrap_or(u64::MAX),
            table_limit: limits.max_tables_per_contract.unwrap_or(u32::MAX),
            table_element_limit,
            type_limit: limits.max_types_per_contract.unwrap_or(u64::MAX),
            global_limit: limits.max_globals_per_contract.unwrap_or(u64::MAX),
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L141-150)
```rust
                wp::Payload::GlobalSection(reader) => {
                    self.ensure_memory_section();
                    self.validator
                        .global_section(&reader)
                        .map_err(|_| PrepareError::Deserialization)?;
                    self.global_limit = self
                        .global_limit
                        .checked_sub(u64::from(reader.count()))
                        .ok_or(PrepareError::TooManyGlobals)?;
                    self.copy_section(SectionId::Global, reader.range())?;
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L209-229)
```rust
                wp::Payload::ElementSection(reader) => {
                    self.ensure_export_section();
                    self.validator
                        .element_section(&reader)
                        .map_err(|_| PrepareError::Deserialization)?;
                    self.copy_section(SectionId::Element, reader.range())?;
                }
                wp::Payload::DataCountSection { count, range } => {
                    self.ensure_export_section();
                    self.validator
                        .data_count_section(count, &range)
                        .map_err(|_| PrepareError::Deserialization)?;
                    self.copy_section(SectionId::DataCount, range.clone())?;
                }
                wp::Payload::DataSection(reader) => {
                    self.ensure_export_section();
                    self.validator
                        .data_section(&reader)
                        .map_err(|_| PrepareError::Deserialization)?;
                    self.copy_section(SectionId::Data, reader.range())?;
                }
```

**File:** runtime/runtime-params-estimator/src/cost.rs (L748-750)
```rust
    /// Invocation cost with 100k zero-initialized globals.
    /// Exposes unbounded per-call Wasmtime global re-initialization not covered by gas.
    AdversarialLoadManyGlobals,
```

**File:** runtime/runtime-params-estimator/src/cost.rs (L751-753)
```rust
    /// Invocation cost with 50k active data segments.
    /// Exposes unbounded per-call data-segment initialization not covered by gas.
    AdversarialLoadManyDataSegments,
```

**File:** runtime/runtime-params-estimator/src/cost.rs (L754-756)
```rust
    /// Invocation cost with 10k active element segments.
    /// Exposes unbounded per-call table-initialization work not covered by gas.
    AdversarialLoadManyElementSegments,
```

**File:** runtime/runtime-params-estimator/src/vm_estimator.rs (L168-184)
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
```

**File:** runtime/runtime-params-estimator/src/vm_estimator.rs (L186-224)
```rust
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

**File:** docs/architecture/gas/README.md (L183-199)
```markdown
The most fundamental dynamic gas cost is `wasm_regular_op_cost`. It is
multiplied by the exact number of WASM operations executed. You can read about
[Gas Instrumentation](https://nomicon.io/RuntimeSpec/Preparation#gas-instrumentation)
if you are curious how we count WASM ops.

Currently, all operations are charged the same, although it could be more
efficient to charge less for opcodes like `i32.add` compared to `f64.sqrt`.

The remaining dynamic costs are for work done during host function calls. Each
host function charges a base cost. Either the general `wasm_base` cost, or a
specific cost such as `wasm_utf8_decoding_base`, or sometimes both. New host
function calls should define a separate base cost and not charge `wasm_base`.

Additional host-side costs can be scaled per input byte, such as
`wasm_sha256_byte`, or costs related to moving data between host and guest, or
any other cost that is specific to the host function. Each host function must
clearly define what its costs are and how they depend on the input.
```
