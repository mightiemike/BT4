### Title
Unbounded per-call WASM instantiation cost (globals/data/element segments) not covered by gas metering - ([File: runtime/near-vm-runner/src/wasmtime_runner/mod.rs])

### Summary
The bref advisory's root cause is a slow, attacker-sized string operation that is performed *before* the runtime can charge/bill proportionally for it. The nearcore analog is structurally the same class of bug (CWE-400, work performed without commensurate gas billing) in contract instantiation: a contract deployed with a very large number of globals, active data segments, or active element segments causes Wasmtime to spend real, size-proportional CPU on *every call* during instantiation, while the only gas charged for "loading" the contract is `contract_loading_base + contract_loading_bytes * code_len` — i.e., proportional to the compiled code size, not to the number of globals/segments that must be initialized on each instantiation.

### Finding Description
When a `FunctionCall` action is applied, `WasmtimeVM::with_compiled_and_loaded` (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs:686-834`) resolves the module (from the on-disk/in-memory compile cache on a cache hit) and then instantiates it fresh for every single call via `pre.instantiate(&mut store)` (`runtime/near-vm-runner/src/wasmtime_runner/mod.rs:1032`). The only cost charged for this pipeline stage is `add_contract_loading_fee` (`runtime/near-vm-runner/src/logic/gas_counter.rs:216-227`):

```
self.pay_per(ExtCosts::contract_loading_bytes, code_len)?;
self.pay_base(ExtCosts::contract_loading_base)
```

This is a function purely of the serialized/compiled code length, charged once via `before_loading_executable`/`after_loading_executable` (`gas_counter.rs:234-272`). It does **not** scale with the number of globals, active data segments, or active element segments the module declares — yet the runtime-params-estimator itself documents that these exact three module properties create real, unbilled CPU cost on every call:

```
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
(`runtime/runtime-params-estimator/src/cost.rs:747-756`)

The estimator's own benchmark harness (`runtime/runtime-params-estimator/src/vm_estimator.rs:168-223`) explicitly warms the compile cache first and then measures 10 repeated invocations of `prepare(...).run(...)` on a contract with tens of thousands of globals/segments — precisely modeling the "cache-hit, instantiate-only" path that a repeat caller/attacker would exercise on every subsequent `FunctionCall` to the same deployed contract. `docs/RuntimeSpec/Preparation.md:43-51` documents the accepted validation limits (up to `1_000_000` globals, `100_000` data segments) that a contract is allowed to declare — these limits are far higher than what is needed to make per-call instantiation overhead large relative to the flat `contract_loading_*` fee charged.

This differs from the properly-metered UTF-8/UTF-16 string decoding paths (`get_utf8_string`/`get_utf16_string` in `wasmtime_runner/logic.rs:396-487`), which charge `*_decoding_base + *_decoding_byte * len` — a correct per-byte accounting for attacker-controlled string size, matching exactly how the bref/`Riverline` fix should have (but didn't) charge for header size before doing `mb_convert_encoding`.

### Impact Explanation
Under the "Rules" of this review, pure resource-exhaustion / no-value-movement findings must be excluded, and this finding is fundamentally a compute-vs-gas-billing mismatch, i.e., a **gas-metering bypass**: an attacker (any contract deployer, reachable by any subsequent caller including themselves) pays only the flat, size-of-code-based `contract_loading_base/bytes` fee, yet forces every future block producer that executes a `FunctionCall` against that contract to perform CPU work proportional to the number of globals/data/element segments — work that is not billed in gas at all. This is the same "attacker pays for a small operation, node performs a disproportionately large one" pattern as CVE-2024-29186, just realized through contract deployment + repeated calls instead of a multipart HTTP body. Repeated calls to such a contract (each individually cheap in gas) let an attacker force outsized real CPU time per chunk on validators relative to the gas they burn, degrading chunk-application throughput without paying commensurate gas — a gas-bypass / compute-DoS vector reachable by an ordinary contract deployer and caller with no privileged access.

### Likelihood Explanation
Deploying a contract and calling it is available to any unprivileged account. The validation limits documented in `docs/RuntimeSpec/Preparation.md` (up to `1_000_000` globals / `100_000` data segments) are large enough, per the estimator's own labeling of these as "adversarial" cases exposing "unbounded" and "not covered by gas" cost, to make this practically triggerable. However, I could not fully confirm (given index limits) whether current mainnet WASM validation additionally imposes tighter `max_functions_number_per_contract`-style caps specifically on globals/data/element segments beyond what `docs/RuntimeSpec/Preparation.md` lists, nor could I directly measure the resulting gas-to-wall-clock ratio in this environment — the runtime-params-estimator code exists specifically to quantify this, but I did not have access to run it or to its historical measured output. This uncertainty affects only the precise severity/likelihood scaling, not the existence of the root-cause gap between "cost charged" (code-size-only) and "cost incurred" (segment/global-count-dependent).

### Recommendation
Charge a gas fee for contract loading that also scales with the number of globals, active data segments, and active element segments declared by the module (in addition to the existing `contract_loading_base`/`contract_loading_bytes`), mirroring how `utf8_decoding_byte`/`utf16_decoding_byte` correctly scale per-byte gas with attacker-controlled input size. Use the runtime-params-estimator's `AdversarialLoadManyGlobals`/`AdversarialLoadManyDataSegments`/`AdversarialLoadManyElementSegments` measurements to derive per-unit costs, and enforce them via a protocol upgrade before this class of contract can be exploited for disproportionate compute cost relative to billed gas.

### Proof of Concept
1. Build a WASM module using helpers already present in the codebase for testing this exact scenario: `many_data_segments_contract(50_000)` and/or `contract_with_num_globals(50_000)` / `many_element_segments_contract(10_000)` (`runtime/near-test-contracts/src/lib.rs:280-350`, referenced from `runtime/runtime-params-estimator/src/vm_estimator.rs:168-184`).
2. Deploy this contract from an ordinary account via a standard `DeployContractAction`; the deploy-time compile cost is charged once and is proportional to compiled code size only.
3. Call the exported `main` method repeatedly via ordinary `FunctionCall` actions/receipts. Each call goes through `WasmtimeVM::with_compiled_and_loaded` → cache hit (no recompilation) → `pre.instantiate(&mut store)` (`wasmtime_runner/mod.rs:1032`), paying only `contract_loading_base + contract_loading_bytes * code_len` gas (`gas_counter.rs:216-227`), independent of the 50,000 data segments / globals that Wasmtime must initialize on every instantiation.
4. Compare wall-clock instantiation time (as measured by the estimator's `measure_instantiation_overhead`, `vm_estimator.rs:186-223`) against the gas actually burned for the call; the estimator code's own doc comments assert this ratio is "unbounded" and "not covered by gas," confirming the billing/compute mismatch reachable purely through a standard deploy + call flow.