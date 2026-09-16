### Title
Unbounded memory allocation from attacker-controlled array/retdata size in Cairo syscall reads (`read_felt_array` / `felt_range_from_ptr`) - ([File: crates/blockifier/src/execution/execution_utils.rs])

### Summary
`felt_range_from_ptr` and its callers `read_felt_array` (both the Cairo1 syscall version and the deprecated Cairo0 syscall version) convert an attacker-controlled length value taken directly from VM memory into a `usize` and use it to request a range of that many felts from the VM, with no upper bound check against any resource limit before the allocation is attempted. This mirrors the Dulwich bug class: a declared size field controls a memory allocation with no relationship to a bounded, already-paid-for resource.

### Finding Description
`felt_range_from_ptr` reads `size` integers from the VM and collects them into a `Vec<Felt>`: [1](#0-0) 

This `size` is derived from attacker-controlled Cairo memory in two call sites:

1. In the current (Cairo1) syscall calldata reader, `size` is the raw pointer difference `array_data_end_ptr - array_data_start_ptr`, which a contract can set to an arbitrary (huge) value with O(1) cost (two relocatable writes), before any bytes are actually written or checked to exist: [2](#0-1) 

2. In the deprecated (Cairo0) syscall calldata reader, `array_size` is read as a single felt from contract-controlled memory and converted straight into `usize` via `usize::try_from(array_size.to_biguint())`, again with no bound checking against the array's actual backing data before being handed to `felt_range_from_ptr`: [3](#0-2) 

`read_execution_retdata` has the same pattern for return-data size after a nested call: [4](#0-3) 

Notably, the codebase has *already* recognized and patched this exact class of bug elsewhere: `CairoArray::try_from` in `apollo_cairo_utils` explicitly validates a contract-controlled length felt against the number of remaining felts before calling `Vec::with_capacity`, with a comment stating the purpose is "to prevent a contract-controlled length felt from triggering an unbounded `Vec::with_capacity`": [5](#0-4) 

That fix, however, only covers retdata deserialization for typed structs in `apollo_cairo_utils` (explicitly noted as having "no production caller yet"). The core syscall calldata/retdata plumbing used by every contract call (`read_felt_array`, `felt_range_from_ptr`, `read_execution_retdata`) does not apply an equivalent check: it converts the attacker/contract-declared length straight to `usize` and passes it into `felt_range_from_ptr`/`vm.get_integer_range`, which will attempt to size a `Vec<Felt>` allocation (or an internal buffer) proportional to that declared length before validating it against the real, bounded amount of memory actually populated for the call.

This is structurally identical to the Dulwich flaw: a small, cheap, attacker-crafted input (a couple of felt writes inside a contract, reachable from any transaction that triggers a `call_contract`/`library_call`/legacy syscall or return path) declares a huge "size", and the sequencer allocates memory based on that declared size rather than on the amount of data actually delivered/verified.

### Impact Explanation
Any account can deploy or declare a contract that, when called (directly or via an invoke transaction with attacker-supplied calldata driving a call/return path), sets the array-size/retdata-size felt to a very large value. This is reachable in gateway validation flows (transaction execution / fee estimation), in blockifier execution during block building, and in Starknet OS re-execution — all "single submitted transaction" entry points explicitly in-scope. If the resulting allocation attempt exceeds available memory, the process handling execution (sequencer during proposal, or any node re-executing the block for validation/sync) aborts. Because this happens deterministically for a given transaction, all honest nodes executing that transaction would crash identically, which can render the network unable to confirm new blocks containing that transaction (liveness/DoS), analogous to the network-availability impact described for Dulwich's `receive-pack`.

### Likelihood Explanation
Likelihood is high in terms of reachability (ordinary calldata via `call_contract`/`library_call`/deprecated syscalls, or a manipulated return value from a nested call, all controllable from a single deployed contract and invoke transaction), and the cost to the attacker to set up the huge size value is O(1) (a couple of felt/pointer writes), independent of the declared size. The exact severity depends on internal `cairo-vm` behavior for `get_integer_range` (whether it eagerly reserves capacity for the whole requested range before validating memory presence) — I was not able to inspect the `cairo-vm` crate's implementation directly since it is an external dependency, so I cannot fully confirm whether `get_integer_range` pre-allocates before checking memory bounds cell-by-cell. This is a real uncertainty that should be verified in the `cairo-vm` dependency source before treating this as fully proven.

### Recommendation
Apply the same defense already used in `apollo_cairo_utils::CairoArray::try_from` to the syscall calldata/retdata paths: before calling `felt_range_from_ptr`, validate the declared `size` against a hard upper bound (e.g., the transaction's remaining gas/resource budget, or a configured max calldata/retdata length) and reject with an explicit error if exceeded, rather than passing an unbounded attacker-controlled `usize` straight into the VM range read.

### Proof of Concept
Conceptual PoC (cannot be fully executed without cairo-vm internals access):
1. Deploy a Cairo0 (or Cairo1) contract whose entry point invokes `library_call` / `call_contract`, constructing the calldata array descriptor such that the size field (`array_size` for Cairo0, or `array_data_end_ptr - array_data_start_ptr` for Cairo1) is set to a very large integer (e.g., close to `usize::MAX`) using a single memory write/pointer arithmetic — no actual large data buffer needs to be written.
2. Submit an ordinary invoke transaction that calls this entry point.
3. During execution (in the gateway's simulate/validate path, in the sequencer's block building, or in full-node/OS re-execution), `read_calldata`/`read_felt_array` computes this huge `size` and calls `felt_range_from_ptr(vm, ptr, size)`, which requests a range of `size` felts from the VM — triggering a large allocation attempt disproportionate to any actual resource paid for by the transaction. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L931-950)
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
}
```

**File:** crates/apollo_cairo_utils/src/lib.rs (L108-131)
```rust
    fn try_from(retdata: Retdata) -> Result<Self, Self::Error> {
        let mut iter = retdata.0.into_iter();

        // The first Felt in the Retdata must be the number of structs in the array.
        let raw_num_items = Felt::try_from_iter(&mut iter)?;

        let num_items = usize::try_from(raw_num_items).map_err(|_| {
            RetdataDeserializationError::USizeConversionError { felt: raw_num_items }
        })?;

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
