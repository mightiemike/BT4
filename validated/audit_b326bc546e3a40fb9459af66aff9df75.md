### Title
Zero gas charged for failed contract preparation/compilation on `FunctionCall` — WASM preparation gas bypass - (File: runtime/near-vm-runner/src/wasmtime_runner/mod.rs)

### Summary
`WasmtimeVM::with_compiled_and_loaded` performs the (potentially expensive) contract loading/deserialization/`prepare_v3` instrumentation pass before any gas is charged. When that pass fails with a `CompilationError` (e.g. `PrepareError::TooManyLocalsPerContract`, `TooManyFunctionsPerContract`, deserialization failure, bad imports), the function returns `PreparationResult::OutcomeAbort(FunctionCallError::CompilationError(e))` **without ever calling `GasCounter::after_loading_executable`**, the only code path on the currently active protocol version that charges the contract-loading fee. The result is a `VMOutcome` with `burnt_gas = 0` / `used_gas = 0` even though real CPU work (WASM parsing + finite-wasm instrumentation) was performed. This mirrors the minievm H-07 pattern: real execution/compute work happens, but the gas accounting path is skipped on a specific error branch, allowing the work to be triggered essentially for free.

### Finding Description
`with_compiled_and_loaded` resolves/compiles/deserializes the contract, then only afterwards charges a loading fee: [1](#0-0) 

- `before_loading_executable` only pre-charges the loading fee `if config.fix_contract_loading_cost` — which is `false` on the currently active mainnet protocol version: [2](#0-1) 
- `after_loading_executable` is the *only* remaining place that charges this fee (legacy post-load ordering), but it is only invoked in the `Ok(res)` branch of the `match pre_result` in `with_compiled_and_loaded`: [3](#0-2) [4](#0-3) 

When compilation/preparation of the contract fails (`pre_result` is `Err(e)`, i.e. a `CompilationError::PrepareError`/similar), the code takes the `Err(e)` branch and returns `PreparationResult::OutcomeAbort(FunctionCallError::CompilationError(e))` directly — `after_loading_executable` is never called, so `add_contract_loading_fee` (which charges `contract_loading_base + contract_loading_bytes * code_len`) never runs. Yet the actual work — module deserialization, `prepare_v3`/`instrument_v3` gas+stack instrumentation over the whole function/local/table set — has already been performed by the node: [5](#0-4) 

The protocol-model spec confirms this is the live, non-gated behavior on the current stable release (`fix_contract_loading_cost` false on 2.13.0/PV 86; the fix is nightly-only PV 129): [6](#0-5) 

The dedicated regression test proves the zero-gas outcome for exactly this scenario on the pre-fix (i.e. currently active) protocol version, while the post-fix protocol version charges substantial, non-zero gas (up to ~98 Ggas) for the same operation, demonstrating the fee is intentionally non-trivial and is being silently skipped today: [7](#0-6) 

### Impact Explanation
Any account can deploy a WASM contract crafted to fail `prepare_v3` (excessive locals, excessive functions, malformed imports, etc. — bounded only by outer `LimitConfig` caps that still allow substantial contract sizes) and then dispatch cheap `FunctionCall` receipts against it. Each first-time (cache-miss) call forces the validating node to do the deserialization + finite-wasm gas/stack instrumentation pass over the whole module, then aborts with `CompilationError` and **charges zero gas** for that CPU work — only the flat, tiny receipt/action base fees are paid. By deploying successive distinct malformed contracts (varying bytes to avoid the compiled-artifact cache), an attacker can force the network to repeatedly perform real, non-trivial preparation/compute work while paying none of the gas fee the protocol itself prices for it (confirmed non-zero, sometimes very large, once the fix is active). This is a genuine fee/gas-bypass usable to cheaply consume validator CPU relative to gas paid — a resource-exhaustion/DoS vector reachable from an ordinary transaction signer.

### Likelihood Explanation
High reachability: deploying a contract and calling a method on it are both ordinary, unprivileged transaction actions available to any signer/RPC caller. The only cost to the attacker is the one-time storage deposit for deploying each new malformed contract account and the flat per-call receipt fees; the exploitable gas gap (loading/instrumentation cost vs. zero gas charged) is directly demonstrated by the existing regression test comparing pre-fix and post-fix protocol versions.

### Recommendation
Charge the contract-loading fee (`after_loading_executable`/`add_contract_loading_fee`) unconditionally before or immediately upon a `CompilationError`/`PrepareError` outcome, regardless of whether `pre_result` is `Ok` or `Err`, so that failed preparation work is billed the same way successful loads are. Equivalently, promote the `fix_contract_loading_cost` behavior (pre-charging before compilation) to be always-on rather than gated behind a not-yet-activated nightly protocol feature on the current stable chain.

### Proof of Concept
1. Deploy a contract whose WASM is a `near_test_contracts::LargeContract` configured to exceed `max_locals_per_contract` or `max_functions_number_per_contract` (as used in the existing test), or any WASM that fails `prepare_v3` deserialization/instrumentation.
2. Submit a `FunctionCall` action/receipt against that account (any method name) on the currently active (non-`FixContractLoadingCost`) protocol version.
3. Observe the resulting `VMOutcome`: `burnt_gas = 0`, `used_gas = 0`, `aborted = Some(FunctionCallError::CompilationError(PrepareError::...))`, exactly as reproduced by: [8](#0-7) 
4. Repeat with newly-deployed, byte-varied malformed contracts to force cache misses and repeated, unbilled preparation work, demonstrating the gas-bypass/DoS pattern.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L813-833)
```rust
        let config = Arc::clone(&self.config);
        let result = gas_counter.before_loading_executable(&config, &method, wasm_bytes);
        if let Err(e) = result {
            let result = PreparationResult::OutcomeAbort(e);
            return Ok(PreparedContract { config, gas_counter, result });
        }
        match pre_result {
            Ok(res) => {
                let result = gas_counter.after_loading_executable(&config, wasm_bytes);
                if let Err(e) = result {
                    let result = PreparationResult::OutcomeAbort(e);
                    return Ok(PreparedContract { config, gas_counter, result });
                }
                closure(gas_counter, res)
            }
            Err(e) => {
                let result =
                    PreparationResult::OutcomeAbort(FunctionCallError::CompilationError(e));
                return Ok(PreparedContract { config, gas_counter, result });
            }
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

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L257-272)
```rust
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

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L402-449)
```rust
pub(crate) fn prepare_contract(
    original_code: &[u8],
    features: crate::features::WasmFeatures,
    config: &Config,
    kind: VMKind,
) -> Result<Vec<u8>, PrepareError> {
    let lightly_steamed = PrepareContext::new(original_code, features, config).run()?;

    let analysis = finite_wasm_6::Analysis::new()
        .with_stack(SimpleMaxStackCfg)
        .with_gas(SimpleGasCostCfg {
            regular: u64::from(config.regular_op_cost),
            linear_base: config.linear_op_base_cost,
            linear_unit: config.linear_op_unit_cost,
        })
        .analyze(&lightly_steamed)
        .map_err(|err| {
            tracing::error!(target: "vm", ?err, ?kind, "analysis failed");
            PrepareError::Deserialization
        })?;
    // Make sure contracts can’t call the instrumentation functions via `env`.
    let res = InstrumentContext::new(
        &lightly_steamed,
        "internal",
        &analysis,
        config.regular_op_cost,
        config.limit_config.max_stack_height,
        config.limit_config.max_blocks_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_blocks_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_operand_stack_bytes_per_function.unwrap_or(u64::MAX),
    )
    .run()
    .map_err(|err| {
        use super::instrument_v3::Error;
        match err {
            Error::TooManyBlocksPerFunction => PrepareError::TooManyBlocksPerFunction,
            Error::TooManyBlocksPerContract => PrepareError::TooManyBlocksPerContract,
            Error::TooManyParamsPerFunction => PrepareError::TooManyParamsPerFunction,
            Error::TooManyParamsPerContract => PrepareError::TooManyParamsPerContract,
            Error::OperandStackTooLarge => PrepareError::OperandStackTooLarge,
            err => {
                tracing::error!(target: "vm", ?err, ?kind, "instrumentation failed");
                PrepareError::Serialization
            }
        }
    })?;
```

**File:** protocol-model/spec/contract-vm.md (L36-37)
```markdown
3. `before_loading_executable` (`gas_counter.rs:236`): reject empty `method_name` (`MethodResolveError::MethodEmptyName`); if `fix_contract_loading_cost` is set, pre-charge `add_contract_loading_fee` (`contract_loading_base` + `contract_loading_bytes * code_len`, `gas_counter.rs:225`) — on OOG return `HostError::GasExceeded` as an abort.
4. `after_loading_executable` (`gas_counter.rs:260`): if `fix_contract_loading_cost` is **not** set, charge the loading fee *after* loading instead (legacy ordering). On 2.13.0 mainnet `fix_contract_loading_cost` is `false` (the fix is nightly-only, PV 129), so the loading fee is charged post-load.
```

**File:** runtime/near-vm-runner/src/tests/runtime_errors.rs (L991-1063)
```rust
    #[test]
    fn test_fn_loading_gas_protocol_upgrade_fail_preparing() {
        // This list covers all control flows that are expected to change
        // with the protocol feature.
        // Having a test for every possible preparation error would be even
        // better, to ensure triggering any of them will always remain
        // compatible with versions before this upgrade. Unfortunately, we
        // currently do not have tests ready to trigger each error.

        #[allow(deprecated)]
        test_builder()
            .wat(r#"(module (export "main" (func 0)))"#)
            .protocol_version(FIX_CONTRACT_LOADING_COST)
            .expects(&[
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 0 used gas 0
                    Err: PrepareError: Error happened while deserializing the module.
                "#]],
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 55053273 used gas 55053273
                    Err: PrepareError: Error happened while deserializing the module.
                "#]],
            ]);

        #[allow(deprecated)]
        test_builder()
            .wasm(&bad_import_global("wtf"))
            .protocol_version(FIX_CONTRACT_LOADING_COST)
            .expects(&[
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 0 used gas 0
                    Err: PrepareError: Error happened during instantiation.
                "#]],
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 99714368 used gas 99714368
                    Err: PrepareError: Error happened during instantiation.
                "#]],
            ]);

        #[allow(deprecated)]
        test_builder()
            .wasm(&bad_import_func("wtf"))
            .protocol_version(FIX_CONTRACT_LOADING_COST)
            .expects(&[
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 0 used gas 0
                    Err: PrepareError: Error happened during instantiation.
                "#]],
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 97535778 used gas 97535778
                    Err: PrepareError: Error happened during instantiation.
                "#]],
            ]);

        #[allow(deprecated)]
        test_builder()
            .wasm(&near_test_contracts::LargeContract {
                functions: 101,
                locals_per_function: 9901,
                ..Default::default()
            }
            .make())
            .protocol_version(FIX_CONTRACT_LOADING_COST)
            .expects(&[
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 0 used gas 0
                    Err: PrepareError: Too many locals declared in the contract.
                "#]],
                expect![[r#"
                    VMOutcome: balance 4 storage_usage 12 return data None burnt gas 839345673 used gas 839345673
                    Err: PrepareError: Too many locals declared in the contract.
                "#]],
            ]);
```
