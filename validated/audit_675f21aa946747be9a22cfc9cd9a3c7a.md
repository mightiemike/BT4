### Title
Contract Loading Fee Charged After Expensive VM Work, Enabling Underpriced Execution — (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs`)

### Summary

On mainnet protocol version 86 (2.13.0), `fix_contract_loading_cost` is `false` (the fix is nightly-only, PV 129). This means the contract loading fee (`contract_loading_base + contract_loading_bytes * code_len`) is charged **after** the expensive loading pipeline — `Module::deserialize`, host-function linking, and `instantiate_pre` — has already executed. An unprivileged user can call any deployed contract with `function_call.gas = 0`, forcing every validator to perform the full, size-proportional loading work without paying the loading fee.

### Finding Description

`WasmtimeVM::with_compiled_and_loaded` (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs`) performs the following steps in order:

1. **Expensive work first** — inside `cache.memory_cache().try_lookup(...)` (lines 706–812): cache lookup, `Module::deserialize` (CPU-intensive for large contracts), memory-export resolution, `Linker` construction, `link` (binding all host functions), and `instantiate_pre`. [1](#0-0) 

2. **`before_loading_executable`** (line 816): with `fix_contract_loading_cost = false`, this only rejects an empty method name — it does **not** pre-charge the loading fee. [2](#0-1) 

3. **`after_loading_executable`** (line 823): charges `contract_loading_base + contract_loading_bytes * wasm_bytes` only after all the expensive work is done. [3](#0-2) 

The `after_loading_executable` implementation confirms the legacy ordering: [4](#0-3) 

The `before_loading_executable` confirms the fee is only pre-charged when `fix_contract_loading_cost` is true (which it is not on stable): [5](#0-4) 

The `fix_contract_loading_cost` flag is `false` on mainnet and only flips to `true` at PV 129 (nightly): [6](#0-5) 

The `Config` struct confirms the field: [7](#0-6) 

The `VMOutcome::abort_but_nop_outcome_in_old_protocol` path shows that without `fix_contract_loading_cost`, method-resolve failures also return a zero-gas NOP outcome, leaving loading work uncharged: [8](#0-7) 

### Impact Explanation

An attacker deploys a maximum-size WASM contract (~4 MB, within `max_contract_size`). They then submit `FunctionCall` actions targeting that contract with `gas = 0`. The action execution fee (a fixed base cost, not proportional to contract size) is charged at the signer's shard. On the receiver's shard, every validator performs the full loading pipeline — `Module::deserialize`, host-function linking, `instantiate_pre` — proportional to contract size. `after_loading_executable` then attempts to charge the loading fee, immediately fails with `GasExceeded` (since `prepaid_gas = 0`), and returns an `OutcomeAbort`. The loading work is done but the loading fee is not collected. The attacker pays only the fixed action execution fee, not the size-proportional loading fee, making repeated calls to a large contract cheaper than intended. This can slow chunk processing (non-network-level DoS) and is fixable without a hardfork by enabling `fix_contract_loading_cost`.

The runtime's handling of `VMRunnerError::LoadingError` also returns a zero-gas NOP outcome (though `fix_contract_loading_error` at PV 86 partially mitigates the `Module::deserialize` failure path): [9](#0-8) 

### Likelihood Explanation

Any unprivileged user can trigger this. No special access, validator role, or privileged key is required. The attacker only needs to:
1. Deploy a large WASM contract (a normal `DeployContract` action).
2. Repeatedly call it with `gas = 0` in `FunctionCallAction`.

The in-memory `AnyCache` (weight-bounded LRU) will evict entries under memory pressure, ensuring the expensive deserialization path is hit repeatedly across validators. The attack cost is bounded by the fixed action execution fee per call, not by the loading fee.

### Recommendation

Enable `fix_contract_loading_cost` on stable (accelerate PV 129 to mainnet, or lower its activation version). This moves the `add_contract_loading_fee` call into `before_loading_executable`, so the fee is pre-charged and the expensive loading pipeline is never entered when gas is insufficient: [10](#0-9) 

### Proof of Concept

1. Deploy a WASM contract of maximum allowed size to account `attacker.near`.
2. Submit repeated `SignedTransaction`s with a single `FunctionCallAction { method_name: "main", args: [], gas: 0, deposit: 0 }` targeting `attacker.near`.
3. Each receipt execution causes every validator to run `Module::deserialize` + `link` + `instantiate_pre` on the full contract binary.
4. `after_loading_executable` charges the loading fee, finds `burnt_gas >= gas_limit` (since `gas_limit = min(max_gas_burnt, 0) = 0`), returns `GasExceeded` abort.
5. The outcome records `burnt_gas = 0` for the loading work; the attacker paid only the fixed action execution fee.
6. Repeating at high frequency fills chunk gas budgets with receipts that do expensive loading work at below-cost gas prices, slowing chunk processing.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L706-812)
```rust
        let (wasm_bytes, pre_result) = cache.memory_cache().try_lookup(
            key,
            || {
                is_memory_hit = false;
                let cache_record = cache.get(&key).map_err(CacheError::ReadError)?;
                let (wasm_bytes, module) =
                    if let Some(CompiledContractInfo { wasm_bytes, compiled }) = cache_record {
                        match compiled {
                            CompiledContract::CompileModuleError(err) => {
                                return Ok((
                                    err.size_bytes_approximate() as u64,
                                    to_any((wasm_bytes, Err(err))),
                                ));
                            }
                            CompiledContract::Code(module) => (wasm_bytes, module),
                        }
                    } else {
                        is_cache_hit = false;
                        let Some(code) = contract.get_code() else {
                            return Err(VMRunnerError::ContractCodeNotPresent);
                        };
                        let wasm_bytes = code.code().len() as u64;
                        match self.compile_and_cache(&code, cache)? {
                            Err(err) => {
                                return Ok((
                                    err.size_bytes_approximate() as u64,
                                    to_any((wasm_bytes, Err(err))),
                                ));
                            }
                            Ok(module) => (wasm_bytes, module),
                        }
                    };
                // (UN-)SAFETY: the `module` must have been produced by
                // a prior call to `serialize`.
                //
                // In practice this is not necessarily true. One could have
                // forgotten to change the cache key when upgrading the version of
                // the near_vm library or the database could have had its data
                // corrupted while at rest.
                //
                // There should definitely be some validation in near_vm to ensure
                // we load what we think we load.
                let compiled_size = module.len();
                let module = match unsafe { Module::deserialize(&self.engine, &module) } {
                    Ok(module) => module,
                    Err(err) => {
                        // Propagate failed contract loading as a cached `FunctionCallError`, mirroring
                        // the memory-export check below, so it flows through the fee-charge points
                        // and finalizes as a gas-bearing abort.
                        if self.config.fix_contract_loading_error {
                            let err = FunctionCallError::LoadingError { msg: err.to_string() };
                            return Ok((
                                err.size_bytes_approximate() as u64,
                                to_any((wasm_bytes, Ok(Err(err)))),
                            ));
                        }
                        return Err(VMRunnerError::LoadingError(err.to_string()));
                    }
                };
                let Some(memory) = module.get_export_index(MEMORY_EXPORT) else {
                    let err = FunctionCallError::LinkError { msg: "memory export missing".into() };
                    return Ok((
                        err.size_bytes_approximate() as u64,
                        to_any((wasm_bytes, Ok(Err(err)))),
                    ));
                };
                let remaining_gas = module.get_export_index(REMAINING_GAS_EXPORT);
                let start = module.get_export_index(START_EXPORT);
                let mut linker = Linker::new(&self.engine);
                link(&mut linker, &self.config);
                match linker.instantiate_pre(&module) {
                    Err(err) => {
                        let err = err.into_vm_error()?;
                        Ok((
                            err.size_bytes_approximate() as u64,
                            to_any((wasm_bytes, Ok(Err(err)))),
                        ))
                    }
                    Ok(pre) => {
                        let ResourcesRequired { num_tables, .. } = module.resources_required();
                        // The module `weight` is estimated as its serialized size. This is a
                        // rough approximation as the runtime metadata size is not included.
                        // Should be sufficient for our purposes.
                        Ok((
                            compiled_size as u64,
                            to_any((
                                wasm_bytes,
                                Ok(Ok(PreparedModule {
                                    pre,
                                    memory,
                                    remaining_gas,
                                    start,
                                    num_tables,
                                })),
                            )),
                        ))
                    }
                }
            },
            move |value| {
                let &(wasm_bytes, ref downcast) = value
                    .downcast_ref::<MemoryCacheType>()
                    .expect("downcast should always succeed");

                (wasm_bytes, downcast.clone())
            },
        )?;
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L816-820)
```rust
        let result = gas_counter.before_loading_executable(&config, &method, wasm_bytes);
        if let Err(e) = result {
            let result = PreparationResult::OutcomeAbort(e);
            return Ok(PreparedContract { config, gas_counter, result });
        }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L823-827)
```rust
                let result = gas_counter.after_loading_executable(&config, wasm_bytes);
                if let Err(e) = result {
                    let result = PreparationResult::OutcomeAbort(e);
                    return Ok(PreparedContract { config, gas_counter, result });
                }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L228-248)
```rust
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

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L250-265)
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

**File:** core/parameters/res/runtime_configs/129.yaml (L1-1)
```yaml
fix_contract_loading_cost: { old: false, new: true }
```

**File:** core/parameters/src/vm.rs (L204-206)
```rust
    /// Enable the `FixContractLoadingCost` protocol feature.
    pub fix_contract_loading_cost: bool,

```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4637-4646)
```rust
    pub fn abort_but_nop_outcome_in_old_protocol(
        state: ExecutionResultState,
        error: FunctionCallError,
    ) -> VMOutcome {
        if state.config.fix_contract_loading_cost {
            Self::abort(state, error)
        } else {
            Self::nop_outcome(error)
        }
    }
```

**File:** runtime/runtime/src/function_call.rs (L314-316)
```rust
        Err(VMRunnerError::LoadingError(msg)) => {
            return Ok(VMOutcome::nop_outcome(FunctionCallError::LoadingError { msg }));
        }
```
