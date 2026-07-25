### Title
Post-Load Fee Charging with Structure-Insensitive Loading Fee Allows Adversarially Structured Contracts to Cause Uncharged Validator Work — (`runtime/near-vm-runner/src/logic/gas_counter.rs`, `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`)

---

### Summary

On mainnet (protocol version 86, `fix_contract_loading_cost = false`), the WASM contract loading fee is charged **after** the full loading pipeline completes. The fee formula is `contract_loading_base + contract_loading_bytes × code_len` — it accounts only for raw code size, not for code structure. An unprivileged user can deploy a contract with adversarially many globals, data segments, or element segments that is cheap by the size-based fee but expensive for validators to load on every call. The params estimator explicitly labels three such adversarial cost categories as "not covered by gas." This is the direct nearcore analog of the Notional M-04 gas-bomb: a type-checking/validation step that does unbounded work before confirming the caller has paid for it.

---

### Finding Description

**Execution ordering in `with_compiled_and_loaded`** (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs:683`):

```
1. Cache lookup / compile (expensive: Module::deserialize, link, instantiate_pre)
2. before_loading_executable  ← only checks empty method name; NO gas charge when fix_contract_loading_cost=false
3. after_loading_executable   ← charges contract_loading_base + contract_loading_bytes * code_len
```

The loading fee is charged at step 3, **after** all the expensive work at step 1 has already been done.

The fee formula in `add_contract_loading_fee` (`gas_counter.rs:217`):

```rust
pub(crate) fn add_contract_loading_fee(&mut self, code_len: u64) -> Result<()> {
    self.pay_per(ExtCosts::contract_loading_bytes, code_len)?;
    self.pay_base(ExtCosts::contract_loading_base)
}
```

The code comment immediately above this function explicitly acknowledges the gap:

> "This cost does not consider the structure of the contract code, only the size. This is currently the only loading fee. A fee that takes the code structure into consideration could be added. But since that would have to happen after loading, we cannot pre-charge it."

The params estimator (`runtime/runtime-params-estimator/src/cost.rs:729–737`) names three adversarial cost categories that are **explicitly described as not covered by gas**:

- `AdversarialLoadManyGlobals` — "Exposes unbounded per-call Wasmtime global re-initialization not covered by gas."
- `AdversarialLoadManyDataSegments` — "Exposes unbounded per-call data-segment initialization not covered by gas."
- `AdversarialLoadManyElementSegments` — "Exposes unbounded per-call table-initialization work not covered by gas."

The `FixContractLoadingCost` protocol feature (PV 129, nightly-only) would move the fee charge to `before_loading_executable`, but it is **not active on mainnet** (PV 86). The spec confirms: "On 2.13.0 mainnet `fix_contract_loading_cost` is `false` (the fix is nightly-only, PV 129), so the loading fee is charged post-load."

The `FixContractLoadingError` feature (PV 86, stable) only fixes the case where `Module::deserialize` **fails** (turning a zero-gas nop into a gas-bearing abort). It does not fix the undercharging for contracts that **succeed** loading but have adversarial structure.

---

### Impact Explanation

**Fee payment bypass / contract execution flow breakage.** An attacker deploys a contract with many active data segments or element segments (within the `max_contract_size` = 1 MB limit). The code size is small, so the loading fee is small. But on every call, Wasmtime must re-initialize all data segments and element segments, consuming validator CPU time that is not covered by the fee. The attacker pays `contract_loading_base + contract_loading_bytes × small_code_len` but forces validators to perform work proportional to the number of segments. Repeated calls to such a contract constitute a fee-payment bypass and a non-network-level denial of service against chunk processing throughput. The broken invariant is: **the loading fee must bound the actual CPU cost of loading a contract on every invocation**.

---

### Likelihood Explanation

The attack requires only two ordinary user actions: deploy a contract (one-time cost) and call it repeatedly. No privileged role, no validator control, no external system dependency. The contract can be crafted to maximize the ratio of loading work to code size. The attack is cheap to sustain because the per-call fee is small (size-based) while the per-call validator work is large (structure-based). The in-memory LRU cache (`AnyCache`) partially mitigates repeated calls to the same contract from the same node, but the attacker can rotate across multiple adversarial contracts to keep the cache cold, or rely on the fact that different validators may not share the same in-memory cache state.

---

### Recommendation

1. **Enable `FixContractLoadingCost` (PV 129) on mainnet.** Pre-charging the loading fee in `before_loading_executable` ensures the caller has paid before any loading work begins, matching the invariant of the external bug's recommended fix ("replace the try-catch pattern with a low-level function call and check the return value's length before decoding it").

2. **Add structure-sensitive loading fees.** Introduce per-global, per-data-segment, and per-element-segment fees charged at deploy time (when the structure is known) or at call time via a pre-scan of the compiled artifact's metadata. The params estimator already has the measurement infrastructure (`AdversarialLoadManyGlobals`, `AdversarialLoadManyDataSegments`, `AdversarialLoadManyElementSegments`).

3. **Tighten structural limits.** Lower `max_functions_number_per_contract`, add explicit limits on the number of active data segments and element segments, so the worst-case loading cost per byte is bounded.

---

### Proof of Concept

```wat
;; Adversarial contract: small code, many active data segments.
;; Each data segment forces Wasmtime to copy bytes into linear memory on every instantiation.
(module
  (memory 1)
  ;; Repeat the following pattern N times (N up to the data-segment limit):
  (data (i32.const 0) "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
  ;; ... (N-1 more data segments) ...
  (func (export "main"))
)
```

Deploy this contract. Call `main` repeatedly. Each call triggers `instantiate_pre` → `instantiate` which re-initializes all N data segments. The gas charged is `contract_loading_base + contract_loading_bytes × code_len` (small, because the code is small), but the actual CPU work scales with N × segment_size. The params estimator's `AdversarialLoadManyDataSegments` benchmark (50k segments) demonstrates the gap between charged gas and actual validator CPU cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L209-220)
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L814-835)
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
```

**File:** runtime/runtime-params-estimator/src/cost.rs (L729-737)
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

**File:** core/primitives-core/src/version.rs (L577-582)
```rust
            ProtocolFeature::FixContractLoadingError => 86,
            ProtocolFeature::RejectEmptyMethodName => 87,

            // Nightly features:
            ProtocolFeature::FixContractLoadingCost => 129,
            // TODO(#11201): When stabilizing this feature in mainnet, also remove the temporary code
```

**File:** core/parameters/res/runtime_configs/129.yaml (L1-1)
```yaml
fix_contract_loading_cost: { old: false, new: true }
```

**File:** protocol-model/spec/contract-vm.md (L36-37)
```markdown
3. `before_loading_executable` (`gas_counter.rs:236`): reject empty `method_name` (`MethodResolveError::MethodEmptyName`); if `fix_contract_loading_cost` is set, pre-charge `add_contract_loading_fee` (`contract_loading_base` + `contract_loading_bytes * code_len`, `gas_counter.rs:225`) — on OOG return `HostError::GasExceeded` as an abort.
4. `after_loading_executable` (`gas_counter.rs:260`): if `fix_contract_loading_cost` is **not** set, charge the loading fee *after* loading instead (legacy ordering). On 2.13.0 mainnet `fix_contract_loading_cost` is `false` (the fix is nightly-only, PV 129), so the loading fee is charged post-load.
```

**File:** protocol-model/spec/contract-vm.md (L92-92)
```markdown
- **`FixContractLoadingCost`** — **nightly only, PV 129** (`version.rs:579`); **not active on 2.13.0**. When enabled, `fix_contract_loading_cost` pre-charges the loading fee in `before_loading_executable` and makes loading-phase failures `abort` (committed) rather than `nop_outcome`; on stable it stays `false`, so the fee is charged post-load and loading-phase resolve errors return NOP outcomes (`gas_counter.rs:248`/`:265`, `logic.rs:4533` `abort_but_nop_outcome_in_old_protocol`).
```
