Based on my investigation, I found a strong analog in `crates/blockifier/src/execution/deprecated_entry_point_execution.rs`.

### Title
Panic via unchecked HashMap index on entry_points_by_type in Cairo0 (deprecated) class execution - (File: crates/blockifier/src/execution/deprecated_entry_point_execution.rs)

### Summary
`resolve_entry_point_pc` in the deprecated (Cairo0) entry-point resolution path indexes a `HashMap<EntryPointType, Vec<EntryPointV0>>` with `&compiled_class.entry_points_by_type[&call.entry_point_type]` [1](#0-0)  instead of using a fallible `.get(...)`. This mirrors the reported TensorFlow bug class: code assumes a nested/keyed lookup will always succeed based on an implicit invariant about the data's shape, but that invariant is derived from data that originates from outside the trusted core (here, a declared Cairo0 contract class), and if violated causes an unchecked panic (Rust's `HashMap` `Index` panics when the key is absent) rather than the `Result`-based `PreExecutionError` that this exact function otherwise consistently returns for the *empty vector*/`None` cases just a few lines below (lines 137–152) [2](#0-1) .

### Finding Description
`resolve_entry_point_pc` is reached whenever a deprecated (Cairo0) class's entry point is invoked during execution (e.g., an `INVOKE`, `L1_HANDLER`, or constructor call against a legacy contract) [3](#0-2) . The code path carefully handles the "no entry points of this type" case for the *values* (empty `Vec`) via `filtered_entry_points.is_empty()` and `entry_points_of_same_type.first()` returning `None`, converting that into `PreExecutionError::NoEntryPointOfTypeFound` [4](#0-3) . However, this graceful handling only occurs if the `EntryPointType` key exists in the map at all (mapping to an empty `Vec`). If the *key itself* is missing from `entry_points_by_type` — e.g. a Cairo0 class is declared/constructed without a `Constructor` or `L1Handler` entry in that map — the direct index operation `entry_points_by_type[&call.entry_point_type]` panics before the graceful-empty-vector logic is ever reached, exactly analogous to the TF advisory's pattern where the second/nested lookup is assumed to succeed once the first indicates a plausible path, but isn't actually guaranteed and a maliciously/adversarially shaped input can violate that assumption.

I was not able to fully confirm within available context whether `starknet_api`'s deserialization of `deprecated_contract_class.rs`'s `entry_points_by_type` always guarantees all three `EntryPointType` keys are present (which would make this unreachable), or whether it is populated by a `#[serde(default)]`/partial map that could legitimately omit a key when a class is deserialized from an externally-declared or feeder-gateway-sourced class file. This is the critical open question for exploitability; the grep for `deprecated_contract_class.rs`'s definition did not resolve in the final pass due to a tool call formatting issue, so I could not verify the exact serialization guarantee.

### Impact Explanation
If the map can be constructed/deserialized with a missing key (as opposed to only an empty `Vec`) for a given `EntryPointType`, then any node executing a transaction that calls an entry point of that (missing-key) type on the affected Cairo0 class would panic. In the sequencer's execution/consensus flow, an uncaught panic during transaction execution can crash the executing thread/process, which is a node-availability / block-production issue rather than a state-divergence issue — this would map to "a network unable to confirm new transactions" if reachable deterministically from a single declared class + a single invoke transaction, since every honest node executing that same class/tx would hit the same panic.

### Likelihood Explanation
Uncertain/Low-to-Medium. This requires (1) confirming that `entry_points_by_type` for Cairo0 classes can, in fact, omit one of the three well-known keys (`Constructor`, `External`, `L1Handler`) rather than always defaulting to an empty vector during deserialization/validation, and (2) that no earlier gateway/class-manager validation step normalizes or rejects such a class before it reaches execution. I could not verify either of these within the available tool budget.

### Recommendation
Replace the direct index operation `&compiled_class.entry_points_by_type[&call.entry_point_type]` with a fallible lookup (`.get(&call.entry_point_type)`), treating a missing key identically to an existing-but-empty vector by falling through to the existing `PreExecutionError::NoEntryPointOfTypeFound` branch, consistent with the "never panic on data reachable from requests" guideline already documented in this repository's own code-style rules [5](#0-4) .

### Proof of Concept
Not conclusively demonstrable without confirming the deserialization/validation guarantee on `entry_points_by_type` for Cairo0 classes (see open question above). Conceptually: declare a Cairo0 class whose JSON `entry_points_by_type` omits the `L1_HANDLER` key entirely (as opposed to providing `"L1_HANDLER": []`), then submit an `L1_HANDLER` transaction targeting that class; if the deserialization path preserves the omission as a missing map key, `resolve_entry_point_pc` would panic at the indexing operation instead of returning `PreExecutionError::NoEntryPointOfTypeFound`.

**Given the unresolved uncertainty about reachability (whether the map can ever have a missing key vs. always an empty `Vec`), I cannot assert this with full confidence as a proven vulnerability** — flagging it as a candidate for further verification rather than a confirmed finding.

### Citations

**File:** crates/blockifier/src/execution/deprecated_entry_point_execution.rs (L120-130)
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
```

**File:** crates/blockifier/src/execution/deprecated_entry_point_execution.rs (L136-152)
```rust
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
```

**File:** .claude/rules/code-style.md (L67-70)
```markdown
### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```
