## Title
Unmetered O(n) linear entry-point selector scan on every contract call enables sequencer CPU-griefing via bloated declared classes - (File: `crates/blockifier/src/execution/contract_class.rs`)

### Summary
The reported bug class (unbounded linear search over attacker-controlled arrays causing disproportionate off-chain/native compute relative to charged gas) has an analog in the sequencer's contract-class entry-point resolution path. `EntryPointsByType::get_entry_point` performs a native Rust linear `filter` over the full list of entry points of a given type (`external`/`l1_handler`/`constructor`) for every single call into a contract, and this scan happens in Rust code executed by the blockifier *before* Cairo VM step metering begins.

### Finding Description
`EntryPointsByType::get_entry_point` filters the whole `entry_points_of_same_type` vector on every call resolution: [1](#0-0) 

This is invoked for CASM-based (V1) classes both for the CairoVM runner path and the Native path: [2](#0-1) [3](#0-2) 

An equivalent linear filter exists for deprecated (Cairo 0 / V0) classes in `resolve_entry_point_pc`: [4](#0-3) 

Notably, the Starknet OS (`compiled_class.cairo`) already mitigates exactly this class of issue for its own re-execution by requiring entry points to be **strictly sorted**, enabling `search_sorted_optimistic` (an O(log n) lookup) instead of a linear scan: [5](#0-4) [6](#0-5) 

The blockifier, however, does not use a sorted/binary-search structure — it uses `Vec<EP>::iter().filter(...)` — and I could not find any cap on the number of entry points per type (`n_entry_points`) enforced at declare-time in the blockifier's class validation path within the retrieved context. Each entry point in a CASM/Sierra class only needs to occupy a `(selector, offset, builtins)` triple, so an attacker can declare a class containing a very large number of `external` (or `l1_handler`) entry points sharing the entry-point-type bucket, all with distinct selectors, bounded mainly by overall contract-class size limits (bytecode size, Sierra length bounds) rather than by entry-point count specifically.

The key distinction from the reported Solidity bug (which is about *duplicate* values triggering wasted linear-scan comparisons) is that here the linear scan cost is driven purely by the *count* of entries in the selected entry-point-type bucket, and — critically — this scan executes in native Rust before any Cairo-VM step is charged. If the resource/fee accounting for a call is dominated by VM step counts (and syscall gas), and this native pre-resolution filter is not reflected proportionally in the charged fee, then every subsequent call into such a bloated class costs the sequencer real CPU time on each invocation without being charged for by the caller in proportion to that cost.

### Impact Explanation
If the cost of `get_entry_point`/`resolve_entry_point_pc` is not proportionally represented in the transaction's charged fee/gas, an attacker can:
1. Declare one class with a very large number of entry points of one type.
2. Repeatedly invoke a cheap selector on that class (e.g., via ordinary `invoke` transactions, or via `library_call`/`call_contract` from another contract) to force the sequencer to repeatedly perform an expensive native linear scan on every call, while paying only for the (much cheaper) metered Cairo VM/syscall cost.

This is a resource-accounting mismatch that, if severe enough, degrades batcher/blockifier throughput per unit of paid gas — a form of computational DoS against block building. However, I was **not able to confirm within the available context**:
- whether there is an existing cap on `n_entry_points` per type enforced during class validation/declare (I found no such limit in the retrieved code, but coverage is incomplete),
- the precise fee/gas cost attributed to entry-point resolution relative to VM step costs, and
- whether contract size limits already bound entry-point count tightly enough to make the scan cost negligible in practice.

Given this uncertainty, I cannot conclusively demonstrate concrete loss/freezing of funds, wrong committed state, or an inability to confirm new transactions strictly from the retrieved evidence — the analog is plausible but not proven to reach a Medium+ impact bar without further verification of entry-point-count limits and fee accounting.

### Likelihood Explanation
Likelihood is limited by two unresolved factors: (1) whether a per-type entry-point count cap already exists elsewhere in class validation that bounds `n_entry_points` to a small constant (which would neutralize the issue), and (2) whether the constant-factor cost of a `Vec::filter` over that many entries is actually large enough, relative to VM step costs already charged, to create a meaningful under-charging gap. Without confirming these, the likelihood of practical exploitability cannot be firmly established from the given context.

### Recommendation
- Verify/enforce an explicit, low bound on the number of entry points per type (`external`, `l1_handler`, `constructor`) at declare-time class validation, and reject classes exceeding it.
- Alternatively, mirror the OS's approach: require declared CASM entry points to be sorted by selector (as already assumed/enforced by `validate_entry_points` in the OS) and use a binary search (e.g., `slice::binary_search_by_key`) in `EntryPointsByType::get_entry_point` and `resolve_entry_point_pc`, replacing the current `O(n)` `iter().filter(...)` scan, to make blockifier lookup cost match the OS's `O(log n)` guarantee and avoid any native-time cost that scales linearly with attacker-chosen entry-point count.
- Ensure gas/fee accounting for entry-point resolution scales with `n_entry_points` if the linear-scan approach is retained, so the cost is charged to the caller.

### Proof of Concept
Not independently reproducible from the retrieved context — I could not confirm the absence of an entry-point-count cap nor measure the actual native-scan cost versus charged gas, both of which would be required to construct a concrete PoC transaction sequence. A background engineering task should (a) check `starknet_api`/blockifier class validation for any `n_entry_points` limit, and (b) benchmark `EntryPointsByType::get_entry_point` cost for classes with thousands of entry points of one type against the current gas charged for a call, to confirm exploitability before treating this as a confirmed vulnerability.

### Citations

**File:** crates/blockifier/src/execution/contract_class.rs (L430-435)
```rust
    pub fn get_entry_point(
        &self,
        entry_point: &EntryPointTypeAndSelector,
    ) -> Result<EntryPointV1, PreExecutionError> {
        self.entry_points_by_type.get_entry_point(entry_point)
    }
```

**File:** crates/blockifier/src/execution/contract_class.rs (L788-809)
```rust
impl<EP: Clone + HasSelector> EntryPointsByType<EP> {
    pub fn get_entry_point(
        &self,
        entry_point: &EntryPointTypeAndSelector,
    ) -> Result<EP, PreExecutionError> {
        entry_point.verify_constructor()?;

        let entry_points_of_same_type = &self[entry_point.entry_point_type];
        let filtered_entry_points: Vec<_> = entry_points_of_same_type
            .iter()
            .filter(|ep| *ep.selector() == entry_point.entry_point_selector)
            .collect();

        match filtered_entry_points[..] {
            [] => Err(PreExecutionError::EntryPointNotFound(entry_point.entry_point_selector)),
            [entry_point] => Ok(entry_point.clone()),
            _ => Err(PreExecutionError::DuplicatedEntryPointSelector {
                selector: entry_point.entry_point_selector,
                typ: entry_point.entry_point_type,
            }),
        }
    }
```

**File:** crates/blockifier/src/execution/native/entry_point_execution.rs (L23-31)
```rust
pub fn execute_entry_point_call(
    call: ExecutableCallEntryPoint,
    compiled_class: NativeCompiledClassV1,
    state: &mut dyn State,
    context: &mut EntryPointExecutionContext,
) -> Result<CallInfo, EntryPointExecutionError> {
    let entry_point = compiled_class
        .get_entry_point(&call.type_and_selector())
        .map_err(EntryPointExecutionError::from)?;
```

**File:** crates/blockifier/src/execution/deprecated_entry_point_execution.rs (L120-159)
```rust
pub fn resolve_entry_point_pc(
    call: &ExecutableCallEntryPoint,
    compiled_class: &CompiledClassV0,
) -> Result<usize, PreExecutionError> {
    if call.entry_point_type == EntryPointType::Constructor
        && call.entry_point_selector != selector_from_name(CONSTRUCTOR_ENTRY_POINT_NAME)
    {
        return Err(PreExecutionError::InvalidConstructorEntryPointName);
    }

    let entry_points_of_same_type = &compiled_class.entry_points_by_type[&call.entry_point_type];
    let filtered_entry_points: Vec<_> = entry_points_of_same_type
        .iter()
        .filter(|ep| ep.selector == call.entry_point_selector)
        .collect();

    // Returns the default entrypoint if the given selector is missing.
    if filtered_entry_points.is_empty() {
        match entry_points_of_same_type.first() {
            Some(entry_point) => {
                if entry_point.selector
                    == EntryPointSelector(StarkHash::from(DEFAULT_ENTRY_POINT_SELECTOR))
                {
                    return Ok(entry_point.offset.0);
                } else {
                    return Err(PreExecutionError::EntryPointNotFound(call.entry_point_selector));
                }
            }
            None => {
                return Err(PreExecutionError::NoEntryPointOfTypeFound(call.entry_point_type));
            }
        }
    }

    if filtered_entry_points.len() > 1 {
        return Err(PreExecutionError::DuplicatedEntryPointSelector {
            selector: call.entry_point_selector,
            typ: call.entry_point_type,
        });
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L118-132)
```text
    // The key must be at offset 0.
    static_assert CompiledClassEntryPoint.selector == 0;
    // TODO(Yoni, 1/1/2026): make sure the cost of searching missing keys is covered
    //   once reverted entrypoints are supported in the OS (should be fine).
    let (entry_point_desc: CompiledClassEntryPoint*, success) = search_sorted_optimistic(
        array_ptr=cast(entry_points, felt*),
        elm_size=CompiledClassEntryPoint.SIZE,
        n_elms=n_entry_points,
        key=execution_context.execution_info.selector,
    );
    if (success != FALSE) {
        return (success=1, entry_point=entry_point_desc);
    }

    return (success=0, entry_point=cast(0, CompiledClassEntryPoint*));
```
