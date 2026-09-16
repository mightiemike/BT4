## Title
Unbounded Linear Scan Per Entry-Point Call Enables Quadratic-Complexity DoS — (File: `crates/blockifier/src/execution/contract_class.rs`, `crates/blockifier/src/execution/deprecated_entry_point_execution.rs`)

### Summary
The Expat `storeAtts()` bug is a classic "N attributes × O(N) scan" quadratic-complexity pattern: for each of N attacker-supplied items, the code performs a linear scan over a data structure whose size also scales with N, yielding O(N²) instead of O(N). An analogous pattern exists in blockifier's entry-point resolution: `EntryPointsByType::get_entry_point` and `resolve_entry_point_pc` resolve a called selector by **linearly filtering** the full list of entry points of a given type on every single call, instead of using a hash/binary lookup. [1](#0-0) [2](#0-1) 

### Finding Description
`EntryPointsByType<EP>::get_entry_point` filters the entire `entry_points_of_same_type` vector for every single call to find the matching selector:
```rust
let filtered_entry_points: Vec<_> = entry_points_of_same_type
    .iter()
    .filter(|ep| *ep.selector() == entry_point.entry_point_selector)
    .collect();
``` [3](#0-2) 

The Cairo0 equivalent, `resolve_entry_point_pc`, does the same linear `.filter()` over `entry_points_of_same_type` on every call for deprecated compiled classes. [4](#0-3) 

A contract's number of entry points of a given type (`external`, `l1_handler`, `constructor`) is controlled by the class declarer (an unprivileged declare-transaction sender) up to the sequencer's `max_contract_bytecode_size`/`max_contract_class_object_size` limits enforced only at the Sierra level in the gateway's `validate_declare_tx`/`validate_entry_points_sorted_and_unique`, which merely checks sortedness/uniqueness, not an upper bound tightly correlated with the number of distinct callable entry points a single invoking transaction can trigger. [5](#0-4) 

A single unprivileged sender can then submit an Invoke transaction whose calldata recursively/iteratively calls into that contract's entry points N times (e.g., via a multicall/recursive account contract), causing the resolution routine to perform O(M) work per call, where M is the number of entry points of that type in the target class. Total cost across the transaction becomes O(N·M) — the same big-O shape as `storeAtts()`'s O(N²) scan over `defaultAtts`, since both N (call count) and M (entry-point count) are attacker-controlled and the per-call cost is not amortized via a hash map or binary search despite `entry_points_by_type` being provably sorted (`validate_entry_points_sorted_and_unique` guarantees ascending order, which the code does not exploit for a binary search).

### Impact Explanation
This causes disproportionate CPU consumption in the sequencer's block-building/execution path relative to the Cairo-step/Sierra-gas budget charged for the call, because the entry-point-resolution scan happens in native Rust code before/outside the metered VM execution loop. In the worst case, a maliciously crafted but well-formed class (many distinct `external` entries, all valid, sorted, unique — passing all gateway checks) combined with a transaction that invokes many distinct selectors on it can inflate wall-clock CPU time non-linearly with the gas actually charged, potentially degrading sequencer throughput or contributing to block-production delays (partial DoS). This does not directly cause loss of funds or wrong state commitment, so severity is bounded to resource-exhaustion/liveness degradation rather than a state-divergence bug.

### Likelihood Explanation
Reaching this path requires only a standard Declare transaction (to create a class with a large number of entry points, within existing bytecode-size limits) followed by an Invoke transaction that calls many distinct entry points on it — both fully within reach of an unprivileged transaction sender, no special privileges, no reliance on node/operator misbehavior.

### Recommendation
Replace the O(M) linear `.filter()` scans in `EntryPointsByType::get_entry_point` (`crates/blockifier/src/execution/contract_class.rs:788-809`) and `resolve_entry_point_pc` (`crates/blockifier/src/execution/deprecated_entry_point_execution.rs:120-166`) with an O(log M) binary search (entry points are already validated to be sorted and unique) or an O(1) hash-map lookup built once when the class is loaded/cached, so per-call cost no longer scales linearly with the number of entry points in the class.

### Proof of Concept
Not independently verified end-to-end (no execution environment available); the flow is derived from static analysis of the cited functions:
1. Declare a Sierra class with the maximum number of sorted, unique `external` entry points allowed under `max_contract_bytecode_size`/`max_contract_class_object_size` (passes `validate_entry_points_sorted_and_unique`).
2. Submit an Invoke transaction (e.g., via an account contract that performs a multicall or recursive call pattern) that calls N distinct entry points on the declared contract within its resource bounds.
3. Each call triggers a full linear scan of the class's entry-point vector in `get_entry_point`/`resolve_entry_point_pc`, so total resolution cost is O(N·M), disproportionate to the linear gas/step cost normally expected for entry-point dispatch.

### Citations

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

**File:** crates/blockifier/src/execution/deprecated_entry_point_execution.rs (L120-166)
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

    // Filtered entry points contain exactly one element.
    let entry_point = filtered_entry_points
        .first()
        .expect("The number of entry points with the given selector is exactly one.");
    Ok(entry_point.offset.0)
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L339-356)
```rust
    fn validate_entry_points_sorted_and_unique(
        &self,
        contract_class: &starknet_api::state::SierraContractClass,
    ) -> StatelessTransactionValidatorResult<()> {
        let is_sorted_unique = |entry_points: &[EntryPoint]| {
            entry_points.windows(2).all(|pair| pair[0].selector < pair[1].selector)
        };

        if is_sorted_unique(&contract_class.entry_points_by_type.constructor)
            && is_sorted_unique(&contract_class.entry_points_by_type.external)
            && is_sorted_unique(&contract_class.entry_points_by_type.l1handler)
        {
            return Ok(());
        }

        Err(StatelessTransactionValidatorError::EntryPointsNotUniquelySorted)
    }
}
```
