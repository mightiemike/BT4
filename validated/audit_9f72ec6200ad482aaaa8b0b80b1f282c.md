### Title
Unbounded allocation from attacker-controlled array-size felt in deprecated (Cairo 0) syscall argument parsing - (File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs)

### Summary
The CVE describes QEMU's `megasas_handle_dcmd` trusting an attacker-supplied `sglist` size field and allocating memory for it without an upper bound, causing host memory exhaustion. The sequencer has an analogous pattern: when executing legacy Cairo 0 contract syscalls (`call_contract`, `library_call`, `emit_event`, etc.), the array length used to read syscall arguments out of VM memory is taken directly from an attacker-controlled felt with no upper-bound validation before it is used to size a memory read/allocation.

### Finding Description
`read_felt_array` in the deprecated syscall path reads the raw `array_size` felt from Cairo VM memory and converts it straight to a `usize`, then passes it, unbounded, into `felt_range_from_ptr`: [1](#0-0) 

`felt_range_from_ptr` forwards this attacker-controlled `size` directly into `vm.get_integer_range(ptr, size)`, a cairo-vm primitive that must allocate a buffer sized to `size` before it can even attempt to validate whether that many memory cells exist: [2](#0-1) 

This helper is the single choke point used by every deprecated-syscall argument/return-data reader that consumes a `(size, data_ptr)` pair, including `read_calldata` (used by `call_contract`, `library_call`, `library_call_l1_handler`, deploy's constructor calldata) and `execute_inner_call`/`read_execution_retdata`: [3](#0-2) [4](#0-3) 

Unlike the modern (Cairo 1) syscall reader, which derives array size from the *difference between two relocatable pointers* (`array_data_end_ptr - array_data_start_ptr`) — see: [5](#0-4) 
the deprecated (Cairo 0) reader trusts a raw felt value directly as the element count with no relation to actual segment contents, and no cap. Because Cairo 0 contract bytecode is not restricted to well-formed compiler output, a contract declarer/deployer can craft bytecode that pushes an arbitrarily large felt (up to `usize::MAX`, since the conversion only fails if the value doesn't fit `usize`) as the "array size" argument to any syscall parsed via `read_felt_array`/`read_calldata`, or as the `retdata_size` returned from an inner call.

This mirrors the code-style guidance the repo itself documents but did not apply here — "cap allocations derived from user input with hard limits," "treat user-provided values as adversarial": [6](#0-5) 
Notably, a similar class of bug was already found and fixed in `apollo_cairo_utils` (retdata array deserialization now validates the declared length against remaining data before allocating): [7](#0-6) 
but the equivalent deprecated-syscall codepath in `blockifier` was not given the same treatment.

### Impact Explanation
Because `read_felt_array`/`felt_range_from_ptr` (deprecated path) are on the deterministic transaction-execution codepath (`blockifier`), any node — sequencer, full node, or the Starknet OS re-execution — that processes a transaction invoking a maliciously-crafted Cairo 0 contract hits the same code with the same attacker-chosen size. A very large declared size causes an attempted huge memory allocation (`Vec`/buffer sized to the attacker value) before any bounds check against actual available memory occurs, which can exhaust host memory or abort the process. Since block execution/re-execution is mandatory and deterministic across all conforming nodes (including provers reproducing the block via the Starknet OS), this can render the network unable to process/confirm the offending transaction or subsequent transactions in the same block, a denial-of-service impact consistent with the CVSS 6.5/Medium rating of the original CVE.

### Likelihood Explanation
Reachable from a single, unprivileged action: declaring a hand-crafted Cairo 0 class (declare transaction) with syscall-invoking bytecode that sets an oversized array-size felt, then invoking it (invoke transaction). No special privileges, operator cooperation, or p2p assumptions are required — matching the in-scope "unprivileged transaction sender, contract deployer, class declarer" surface. The likelihood, however, depends on cairo-vm's actual `get_integer_range` implementation performing an eager allocation proportional to `size` before validating memory presence; this repo does not vendor that code, so this part of the mechanism could not be directly confirmed from source available in this index.

### Recommendation
- In `read_felt_array` (deprecated_syscalls/hint_processor.rs), before converting `array_size` to `usize` and calling `felt_range_from_ptr`, validate the declared size against a sane upper bound (e.g., the same `max_calldata_length`/event size limits already enforced elsewhere) or against the actual number of populated cells in the target segment, analogous to the fix already applied in `apollo_cairo_utils::TryFrom<Retdata> for CairoArray<T>`.
- Apply the same bound to `read_execution_retdata`'s `retdata_size` before calling `felt_range_from_ptr`.
- Consider deriving deprecated-syscall array sizes from pointer differences (as the Cairo 1 path already does) rather than trusting a standalone felt, removing this class of bug entirely.

### Proof of Concept
1. Declare a Cairo 0 class whose bytecode, instead of using the standard compiler-emitted calldata marshalling, pushes an oversized felt (e.g., `2^40`) as the `calldata_len`/array-size argument immediately before invoking `call_contract`, `library_call`, or `emit_event`.
2. Submit an invoke transaction that triggers this entry point.
3. During execution, `read_calldata` → `read_felt_array` (crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:931-949) converts the felt to a `usize` and calls `felt_range_from_ptr(vm, ptr, size)` (crates/blockifier/src/execution/execution_utils.rs:228-237), which invokes `vm.get_integer_range(ptr, size)` with the attacker-chosen size, attempting a large allocation before any real memory-presence check occurs — reproducible deterministically on every node that executes/re-executes this transaction.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L860-875)
```rust
pub fn read_calldata(
    vm: &VirtualMachine,
    ptr: &mut Relocatable,
) -> DeprecatedSyscallExecutorBaseResult<Calldata> {
    Ok(Calldata(read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?.into()))
}

pub fn read_call_params(
    vm: &VirtualMachine,
    ptr: &mut Relocatable,
) -> DeprecatedSyscallExecutorBaseResult<(EntryPointSelector, Calldata)> {
    let function_selector = EntryPointSelector(felt_from_ptr(vm, ptr)?);
    let calldata = read_calldata(vm, ptr)?;

    Ok((function_selector, calldata))
}
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L931-949)
```rust
pub fn read_felt_array<TErr>(vm: &VirtualMachine, ptr: &mut Relocatable) -> Result<Vec<Felt>, TErr>
where
    TErr: From<StarknetApiError>
        + From<VirtualMachineError>
        + From<MemoryError>
        + From<MathError>
        + From<TryFromBigIntError<BigUint>>,
{
    let array_size = felt_from_ptr(vm, ptr)?;
    // An empty array's data pointer may be a felt-zero null (`cast(0, felt*)`) rather than a real
    // segment.
    if array_size == Felt::ZERO {
        *ptr = (*ptr + 1)?;
        return Ok(vec![]);
    }
    let array_data_start_ptr = vm.get_relocatable(*ptr)?;
    *ptr = (*ptr + 1)?;

    Ok(felt_range_from_ptr(vm, array_data_start_ptr, usize::try_from(array_size.to_biguint())?)?)
```

**File:** crates/blockifier/src/execution/execution_utils.rs (L185-199)
```rust
pub fn read_execution_retdata(
    runner: &CairoRunner,
    retdata_size: MaybeRelocatable,
    retdata_ptr: &MaybeRelocatable,
) -> Result<Retdata, PostExecutionError> {
    let retdata_size = match retdata_size {
        MaybeRelocatable::Int(retdata_size) => usize::try_from(retdata_size.to_bigint())
            .map_err(PostExecutionError::RetdataSizeTooBig)?,
        relocatable => {
            return Err(VirtualMachineError::ExpectedIntAtRange(Box::new(Some(relocatable))).into());
        }
    };

    Ok(Retdata(felt_range_from_ptr(&runner.vm, Relocatable::try_from(retdata_ptr)?, retdata_size)?))
}
```

**File:** crates/blockifier/src/execution/execution_utils.rs (L228-237)
```rust
pub fn felt_range_from_ptr(
    vm: &VirtualMachine,
    ptr: Relocatable,
    size: usize,
) -> Result<Vec<Felt>, VirtualMachineError> {
    let values = vm.get_integer_range(ptr, size)?;
    // Extract values as `Felt`.
    let values = values.into_iter().map(|felt| *felt).collect();
    Ok(values)
}
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L860-880)
```rust
pub fn read_felt_array<TErr>(vm: &VirtualMachine, ptr: &mut Relocatable) -> Result<Vec<Felt>, TErr>
where
    TErr: From<StarknetApiError> + From<VirtualMachineError> + From<MemoryError> + From<MathError>,
{
    // If the start and end pointers are the same, the array is empty.
    // This check is necessary to handle the case where both pointers are zero, and thus are not
    // relocatable values.
    let array_start = vm.get_maybe(&*ptr);
    if array_start.is_some() && array_start == vm.get_maybe(&(*ptr + 1_usize)?) {
        *ptr = (*ptr + 2)?;
        return Ok(vec![]);
    }

    let array_data_start_ptr = vm.get_relocatable(*ptr)?;
    *ptr = (*ptr + 1)?;
    let array_data_end_ptr = vm.get_relocatable(*ptr)?;
    *ptr = (*ptr + 1)?;
    let array_size = (array_data_end_ptr - array_data_start_ptr)?;

    Ok(felt_range_from_ptr(vm, array_data_start_ptr, array_size)?)
}
```

**File:** .claude/rules/code-style.md (L62-70)
```markdown
### Treat user-provided values as adversarial
- Any value deserialized from an HTTP request, query parameter, or other external input must be assumed hostile
- Trace user-controlled values through the full call graph — can they cause DoS, OOM, panics, or resource exhaustion?
- Cap allocations derived from user input with hard limits

### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```

**File:** crates/apollo_cairo_utils/src/lib.rs (L118-131)
```rust
        // Each array element consumes at least one Felt, so a declared count larger than the
        // number of remaining Felts cannot be valid. Validate before allocating to prevent a
        // contract-controlled length felt from triggering an unbounded `Vec::with_capacity`.
        let num_remaining_felts = iter.len();
        if num_items > num_remaining_felts {
            return Err(RetdataDeserializationError::InvalidObjectLength {
                message: format!(
                    "declared array length {num_items} exceeds {num_remaining_felts} remaining \
                     retdata felts"
                ),
            });
        }

        let mut result = Vec::with_capacity(num_items);
```
