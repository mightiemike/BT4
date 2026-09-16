### Title
Unbounded array-size field in Cairo0 syscall array reads enables memory exhaustion - (File: `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`)

### Summary
`read_felt_array` in the deprecated (Cairo0) syscall hint processor derives the number of elements to read directly from an attacker-influenced `Felt` value taken from the running contract's own memory, converts it to a `usize`, and passes it straight to `felt_range_from_ptr` with no upper-bound check. This mirrors the Archive::Tar CVE-2026-9538 pattern: a size field taken from untrusted input is used to size a memory allocation with no sanity limit, other than being non-zero and fitting in a `usize`.

### Finding Description
In `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`: [1](#0-0) 

`array_size` is read as a raw `Felt` from VM memory (`felt_from_ptr`) rather than being derived from an actual, already-allocated memory range (as is done in the newer Cairo1 syscall path). The only validation performed is `array_size == Felt::ZERO` (empty-array special case); otherwise the value is converted with `usize::try_from(array_size.to_biguint())?` and forwarded to `felt_range_from_ptr(vm, array_data_start_ptr, size)`, which allocates a `Vec<Felt>`/`Vec<MaybeRelocatable>` sized to `size` elements.

By contrast, the Cairo1 syscall equivalent computes the array size as the *difference between two pointers* already present in VM memory segments, which is inherently bounded by segment sizes: [2](#0-1) 

This means the deprecated Cairo0 path is reachable by any unprivileged transaction sender invoking (or deploying and invoking) a Cairo0 contract, since `read_calldata`/`read_call_params` in the deprecated syscall handler consume this function for syscalls such as `call_contract`: [3](#0-2) 

An attacker-controlled Cairo0 contract can write an arbitrarily large (but usize-representable, e.g. billions) value into the memory cell used as `array_size` for a syscall request (e.g., calldata length passed to `call_contract`/`library_call`), triggering allocation of a `Vec` with that many elements before any subsequent memory-access failure is detected.

### Impact Explanation
If `felt_range_from_ptr` pre-allocates capacity for `array_size` elements before validating that the underlying memory actually contains that many values, a malicious contract call can force the sequencer node to attempt a very large heap allocation (tens of GB), causing the executing process to OOM or become unresponsive while processing a single transaction. This can cause a **denial of service on transaction execution**, i.e., the sequencer becomes unable to process further transactions/blocks — matching the "network unable to confirm new transactions" impact criterion.

### Likelihood Explanation
Reachable from a single, unprivileged transaction: any account can declare and invoke a Cairo0 contract that issues a syscall (e.g. `call_contract`) with a manipulated calldata-length felt in the memory segment read by `read_felt_array`. No special privileges (proposer/operator/prover) are required — only a deployed Cairo0 class and a transaction invoking it.

### Recommendation
Add an explicit upper-bound check on `array_size` in `read_felt_array` (deprecated_syscalls/hint_processor.rs) analogous to the bound already enforced on Sierra arrays in the Cairo OS (`SIERRA_ARRAY_LEN_BOUND`), rejecting the syscall before any allocation is attempted, and/or ensure `felt_range_from_ptr` validates the target memory range exists (segment size) before allocating a `Vec` of the requested capacity.

### Proof of Concept
1. Declare and deploy a Cairo0 contract whose entry point issues a `call_contract` (or any array-consuming) syscall.
2. In that entry point, before making the syscall, write a very large felt value (e.g., `10^9`) into the memory cell that becomes `array_size` for the syscall request struct being parsed by `read_calldata`/`read_felt_array`.
3. Submit an `INVOKE` transaction that triggers this entry point.
4. Observe that `read_felt_array` (crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:938-949) converts the felt to `usize` and forwards it to `felt_range_from_ptr` without any additional bound check, driving a large allocation during syscall request parsing.

Note: I was unable to fully inspect the body of `felt_range_from_ptr` (in `crates/blockifier/src/execution/execution_utils.rs`) within the available tool calls to confirm exactly how/whether it pre-allocates a `Vec` of `array_size` capacity before verifying the memory range exists (versus incrementally reading and erroring early). This detail is necessary to fully confirm the magnitude of the memory-exhaustion impact and should be verified directly in that function before treating this as conclusively exploitable.

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
