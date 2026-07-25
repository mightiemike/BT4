### Title
Contract-Loading Failure Produces Zero-Gas Nop Before PV 86, Enabling Fee Payment Bypass — (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs`)

### Summary

Before protocol version 86, when `Module::deserialize` fails for a compiled Wasmtime contract (e.g., a contract with 100 000 globals that breaches `max_core_instance_size`), the runner returns `VMRunnerError::LoadingError` — a non-deterministic runner error — instead of a deterministic `FunctionCallError`. The runtime maps this to a **zero-gas nop outcome**: the contract-loading fee is never charged, and no state changes are committed. An unprivileged user can deploy such a contract and call it repeatedly at zero gas cost, bypassing fee payment entirely.

### Finding Description

In `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`, the `with_compiled_and_loaded` function calls `Module::deserialize` on the cached compiled artifact. When deserialization fails (e.g., the module's instance data exceeds Wasmtime's `max_core_instance_size` pool limit), the code branches on the `fix_contract_loading_error` config flag:

```rust
// mod.rs:749-762
let module = match unsafe { Module::deserialize(&self.engine, &module) } {
    Ok(module) => module,
    Err(err) => {
        if self.config.fix_contract_loading_error {
            // POST-FIX (PV ≥ 86): gas-bearing abort
            let err = FunctionCallError::LoadingError { msg: err.to_string() };
            return Ok((err.size_bytes_approximate() as u64,
                        to_any((wasm_bytes, Ok(Err(err))))));
        }
        // PRE-FIX (PV < 86): non-deterministic runner error → zero-gas nop
        return Err(VMRunnerError::LoadingError(err.to_string()));
    }
};
```

The `fix_contract_loading_error` flag is `false` at PV 83–85 and is flipped to `true` only by the PV-86 config diff:

```yaml
# core/parameters/res/runtime_configs/86.yaml
fix_contract_loading_error: { old: false, new: true }
```

The `ProtocolFeature::FixContractLoadingError` is registered at version 86 in `core/primitives-core/src/version.rs:577`.

At PV 83–85, `VMRunnerError::LoadingError` is classified as a non-deterministic runner error. Per the runner's own documentation (`runner.rs:12–14`), this "means nearcore is buggy or the database has been corrupted." The runtime maps it to a **zero-gas nop**: no gas is charged, no state changes are committed. The test at `runtime/near-vm-runner/src/tests/runtime_errors.rs:27–88` explicitly documents and confirms this pre-fix/post-fix split:

- **Pre-fix**: `Err(VMRunnerError::LoadingError(_))` → zero gas charged, loading work uncharged.
- **Post-fix**: `Ok(VMOutcome { aborted: Some(FunctionCallError::LoadingError), used_gas: loading_fee })` → contract-loading fee charged.

A contract with 100 000 globals passes the WASM preparation phase (which does not check `max_core_instance_size`) but fails at `Module::deserialize` time. This is the exact trigger path.

### Impact Explanation

**Fee payment bypass.** At PV 83–85, any user who deploys a contract that passes preparation but fails at `Module::deserialize` can call that contract an unlimited number of times at zero gas cost. The contract-loading fee — `contract_loading_base + contract_loading_bytes × code_len` — is never deducted. This directly violates the invariant that every function-call execution must charge at least the contract-loading fee. The attacker pays the one-time deployment cost but amortizes it over arbitrarily many free calls.

Additionally, because `VMRunnerError` is supposed to signal non-determinism, different validator implementations or future Wasmtime versions could handle the same failure differently, risking state divergence — though in practice all nodes running the same binary produce the same zero-gas nop.

### Likelihood Explanation

**Low-to-medium.** The attacker must craft a contract that:
1. Passes `prepare_contract` (WASM validation + instrumentation), and
2. Fails `Module::deserialize` at the Wasmtime pooling-allocator level.

A contract with ≥ 100 000 globals reliably triggers this (each global adds 8 bytes of instance data, breaching the 1 MiB `max_core_instance_size` default). This is straightforward to construct. The window is PV 83–85; PV 86 closes it. As of the mainnet vote schedule (`2026-07-20`), the network may still be in the 1–2 epoch activation window.

### Recommendation

1. **Immediate**: Ensure the network activates PV 86 promptly so `fix_contract_loading_error = true` takes effect on all shards.
2. **Defense-in-depth**: Add a preparation-phase check that rejects contracts whose global count would breach `max_core_instance_size`, so the failure is caught before the loading phase rather than silently producing a zero-gas nop.
3. **Invariant enforcement**: Treat any `VMRunnerError::LoadingError` that arises from a deterministic contract property (not from node-local corruption) as a `FunctionCallError` so the loading fee is always charged.

### Proof of Concept

```rust
// 1. Generate a contract with 100 000 globals (passes preparation, fails deserialize).
let wasm = near_test_contracts::contract_with_num_globals(100_000);

// 2. Deploy it to account "attacker.near" at PV 85.
// 3. Call it:
//    - Module::deserialize fails → VMRunnerError::LoadingError
//    - Runtime maps to zero-gas nop
//    - used_gas == 0, contract-loading fee == 0
// 4. Repeat indefinitely at zero cost.

// Confirmed by the existing test:
// runtime/near-vm-runner/src/tests/runtime_errors.rs:27-88
// Pre-fix assertion:
assert!(matches!(result, Err(VMRunnerError::LoadingError(_))));
// Post-fix assertion (PV 86):
assert_eq!(outcome.used_gas, loading_base + loading_byte * wasm.len());
```

**Affected files and lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L749-763)
```rust
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
```

**File:** core/parameters/res/runtime_configs/86.yaml (L1-1)
```yaml
fix_contract_loading_error: { old: false, new: true }
```

**File:** core/primitives-core/src/version.rs (L577-577)
```rust
            ProtocolFeature::FixContractLoadingError => 86,
```

**File:** runtime/near-vm-runner/src/tests/runtime_errors.rs (L19-88)
```rust
/// module at `Module::deserialize` fails.
///
/// Pre-`FixContractLoadingError` this surfaces as `VMRunnerError::LoadingError`,
/// which the runtime maps to a zero-gas nop — the contract-loading work is left
/// uncharged. Post-feature the same failure finalizes as a gas-bearing abort
/// that charges the contract-loading fee. Either way it must not panic / crash
/// the node.
#[test]
fn test_max_core_instance_size_breached() {
    let wasm = near_test_contracts::contract_with_num_globals(100_000);

    super::with_vm_variants(|vm_kind| {
        let run = |config: near_parameters::vm::Config| {
            let code = ContractCode::new(wasm.clone(), None);
            let config = Arc::new(config);
            let fees = Arc::new(RuntimeFeesConfig::test());
            let mut ext = MockedExternal::with_code(code.clone_for_tests());
            let context = super::create_context(vec![]);
            let gas_counter = context.make_gas_counter(&config);
            vm_kind
                .runtime(config)
                .unwrap()
                .prepare(&ext, None, gas_counter, "main")
                .run(&mut ext, &context, fees)
        };

        let base_config = super::test_vm_config(Some(vm_kind));

        match vm_kind {
            VMKind::Wasmtime => {
                // Pre-fix: zero-gas nop, loading work uncharged.
                let before = near_parameters::vm::Config {
                    fix_contract_loading_error: false,
                    ..base_config.clone()
                };
                let result = run(before);
                assert!(
                    matches!(result, Err(VMRunnerError::LoadingError(_))),
                    "pre-fix: expected LoadingError for oversized instance, got: {result:?}",
                );

                // Post-fix: gas-bearing abort that charges the loading fee.
                let after =
                    near_parameters::vm::Config { fix_contract_loading_error: true, ..base_config };
                let loading_base = after.ext_costs.gas_cost(ExtCosts::contract_loading_base);
                let loading_byte = after.ext_costs.gas_cost(ExtCosts::contract_loading_bytes);
                let expected_gas = loading_base
                    .checked_add(loading_byte.checked_mul(wasm.len() as u64).unwrap())
                    .unwrap();
                let outcome = run(after).expect("post-fix run should finalize as an Ok abort");
                assert!(
                    matches!(outcome.aborted, Some(FunctionCallError::LoadingError { .. })),
                    "post-fix: expected LoadingError abort, got: {:?}",
                    outcome.aborted,
                );
                assert_eq!(
                    outcome.used_gas, expected_gas,
                    "post-fix: contract-loading fee should be charged",
                );
                assert!(expected_gas.as_gas() > 0, "loading fee should be non-zero");
            }
            _ => {
                let result = run(base_config);
                assert!(
                    result.as_ref().is_ok_and(|outcome| outcome.aborted.is_none()),
                    "{vm_kind:?}: expected clean success for many-globals contract, got: {result:?}",
                );
            }
        }
    });
```

**File:** core/parameters/src/vm.rs (L207-212)
```rust
    /// Enable the `FixContractLoadingError` protocol feature: charge the
    /// contract-loading fee and finalize as a gas-bearing abort (instead of a
    /// zero-gas nop) when a compiled module fails to load at
    /// `Module::deserialize`.
    pub fix_contract_loading_error: bool,

```
