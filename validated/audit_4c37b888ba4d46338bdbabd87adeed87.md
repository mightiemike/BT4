### Title
Contract-loading (VM instantiation) gas is charged after the expensive deserialize/link/instantiate work runs, enabling a cheap DoS - (File: `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`, `runtime/near-vm-runner/src/logic/gas_counter.rs`)

### Summary
The nearcore WASM runtime performs contract deserialization, host-function linking, and `instantiate_pre` (the "VM spin-up" for a `FunctionCall`) *before* checking or charging the `contract_loading_base`/`contract_loading_bytes` gas fee for that work. On stable protocol (PV 86, `fix_contract_loading_cost == false`), the fee is only applied in `after_loading_executable`, i.e., after the costly loading work has already been executed. This is the exact bug class described in the SEDA Tally-VM report: metered "startup" work is performed unconditionally before the corresponding gas charge/limit check, letting an attacker submit calls whose `prepaid_gas` is deliberately too low to ever pay for loading, while the validator still performs the loading work first.

### Finding Description
`WasmtimeVM::with_compiled_and_loaded` resolves the compiled contract via the two-level cache; on a cache miss it fetches code, compiles/caches it, `deserialize`s the module, resolves the `memory` export, builds a `Linker`, `link`s host functions, and calls `instantiate_pre` — all inside the `try_lookup` closure, unconditionally, with no gas check: [1](#0-0) 

Only *after* this loading work completes does the code call `gas_counter.before_loading_executable` and (on stable) `gas_counter.after_loading_executable`, which is where the loading fee is actually paid and where an out-of-gas condition would be detected: [2](#0-1) 

The gas-charging logic itself confirms the ordering is protocol-version-gated and that the pre-charge ("fix") path is not active on stable: [3](#0-2) 

Per the protocol-model spec for this component, `fix_contract_loading_cost` is a **nightly-only feature at PV 129** and is **`false` on the current stable 2.13.0 (PV 86)** mainnet configuration, meaning the loading fee is charged post-load on the live protocol: [4](#0-3) [5](#0-4) 

This is structurally identical to the SEDA finding: expensive VM-startup work (deserialize + link + instantiate) is executed before the gas limit that should have prevented it is enforced, because the corresponding fee is charged only afterward.

### Impact Explanation
An unprivileged transaction signer can deploy one or more large `FunctionCall`-capable contracts (this deploy cost is paid normally, it is not the exploited step), then send `FunctionCall` transactions with `prepaid_gas` set just above the minimal receipt/`function_call_base` cost but below the contract's `contract_loading_base + contract_loading_bytes * len` fee. For each such call, validating nodes will still perform the full cache-miss path (compile/deserialize/link/instantiate_pre) before discovering — only in `after_loading_executable` — that the accumulated fee exceeds `prepaid_gas`, causing a `GasExceeded` abort. The attacker is only charged whatever fits in the small `prepaid_gas`, while validators absorb the disproportionate CPU cost of decompression, deserialization, linking, and instantiation. Rotating across many distinct large contracts (to keep missing the in-memory `AnyCache`) amplifies this into a sustained, cheap CPU-DoS / chain-slowdown vector — an invalid-cost/gas-bypass condition reachable purely from ordinary transaction submission.

### Likelihood Explanation
This requires no privileged access, no validator collusion, and no protocol bug beyond normal transaction submission and standard contract deployment — both of which are available to any account. The condition is fully deterministic and reproducible given a large enough contract and a `prepaid_gas` value chosen slightly under the loading-fee threshold, and the vulnerable code path (`after_loading_executable` ordering) is confirmed to be active on the current stable protocol version, not merely a historical/removed code path.

### Recommendation
Mirror the mitigation already implemented behind the `fix_contract_loading_cost` nightly feature and make it the default/only path on stable: pre-charge (or at least pre-estimate and gate on) the `contract_loading_base`/`contract_loading_bytes` fee using the known contract size *before* deserialization/linking/`instantiate_pre` run, i.e., always take the `before_loading_executable` branch and abort before performing the expensive work when `prepaid_gas` cannot cover it, rather than gating this behind a not-yet-stabilized protocol feature.

### Proof of Concept
1. Deploy account `A` with a maximally-sized `FunctionCall` contract `C` (near `max_contract_size`), paying normal deployment gas.
2. From account `B`, send a `FunctionCall` transaction to `C` with `prepaid_gas` = `function_call_base fee + a few thousand gas` — deliberately less than `contract_loading_base + contract_loading_bytes * len(C)`.
3. Observe (per the existing test `test_fn_loading_gas_protocol_upgrade_exceed_loading` in `runtime/near-vm-runner/src/tests/runtime_errors.rs`) that the transaction aborts with `Exceeded the prepaid gas`, but only after `with_compiled_and_loaded` has already performed deserialize/link/`instantiate_pre` for `C`, per the code path cited above.
4. Repeat with many distinct large contracts to keep missing the in-memory cache, forcing validators to redo the full uncharged loading work on each call while the attacker's cost stays pinned near the tiny `prepaid_gas` chosen in step 2. [6](#0-5)

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L704-763)
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
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L812-834)
```rust
        crate::metrics::record_compiled_contract_cache_lookup(is_cache_hit, is_memory_hit);
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
    }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L216-272)
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

**File:** protocol-model/spec/contract-vm.md (L32-37)
```markdown
### 3. Compile + load + loading fee (`with_compiled_and_loaded`)
`WasmtimeVM::with_compiled_and_loaded` (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs:683`) resolves the compiled artifact through a two-level cache (see §7), then charges the loading fee. Ordering (load-bearing):
1. Look up `ContractCacheKey` in the in-memory `AnyCache`, then the on-disk cache; on a miss, fetch code via `Contract::get_code` and `compile_and_cache` (`:606`); a compile failure is stored/returned as `CompiledContract::CompileModuleError` (`mod.rs:723`).
2. `deserialize` the module, resolve the `memory` export (missing ⇒ `LinkError`), build a `Linker`, `link` host functions (`mod.rs:1127`), and `instantiate_pre`.
3. `before_loading_executable` (`gas_counter.rs:236`): reject empty `method_name` (`MethodResolveError::MethodEmptyName`); if `fix_contract_loading_cost` is set, pre-charge `add_contract_loading_fee` (`contract_loading_base` + `contract_loading_bytes * code_len`, `gas_counter.rs:225`) — on OOG return `HostError::GasExceeded` as an abort.
4. `after_loading_executable` (`gas_counter.rs:260`): if `fix_contract_loading_cost` is **not** set, charge the loading fee *after* loading instead (legacy ordering). On 2.13.0 mainnet `fix_contract_loading_cost` is `false` (the fix is nightly-only, PV 129), so the loading fee is charged post-load.
```

**File:** protocol-model/spec/contract-vm.md (L92-92)
```markdown
- **`FixContractLoadingCost`** — **nightly only, PV 129** (`version.rs:579`); **not active on 2.13.0**. When enabled, `fix_contract_loading_cost` pre-charges the loading fee in `before_loading_executable` and makes loading-phase failures `abort` (committed) rather than `nop_outcome`; on stable it stays `false`, so the fee is charged post-load and loading-phase resolve errors return NOP outcomes (`gas_counter.rs:248`/`:265`, `logic.rs:4533` `abort_but_nop_outcome_in_old_protocol`).
```

**File:** runtime/near-vm-runner/src/tests/runtime_errors.rs (L932-962)
```rust
    // Executing with just enough gas to load the contract will fail before and
    // after. Both charge the same amount of gas.
    #[test]
    fn test_fn_loading_gas_protocol_upgrade_exceed_loading() {
        let expect = expect![[r#"
            VMOutcome: balance 4 storage_usage 12 return data None burnt gas 79017763 used gas 79017763
            Err: Exceeded the prepaid gas.
        "#]];
        let test_after = test_builder().wat(ALMOST_TRIVIAL_CONTRACT);
        let cfg_costs = &test_after.configs().next().unwrap().wasm_config.ext_costs;
        let loading_base = cfg_costs.gas_cost(ExtCosts::contract_loading_base);
        let loading_byte = cfg_costs.gas_cost(ExtCosts::contract_loading_bytes);
        let wasm_length = test_after.get_wasm().len();
        test_after
            .gas(
                loading_base
                    .checked_add(loading_byte.checked_mul(wasm_length as u64).unwrap())
                    .unwrap(),
            )
            .expect(&expect);
        #[allow(deprecated)]
        test_builder()
            .wat(ALMOST_TRIVIAL_CONTRACT)
            .only_protocol_versions(vec![FIX_CONTRACT_LOADING_COST - 1])
            .gas(
                loading_base
                    .checked_add(loading_byte.checked_mul(wasm_length as u64).unwrap())
                    .unwrap(),
            )
            .expect(&expect);
    }
```
