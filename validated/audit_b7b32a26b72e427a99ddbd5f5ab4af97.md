### Title
Unbounded attacker-controlled array-size felt drives an unchecked Cairo VM allocation in the deprecated (Cairo0) syscall array reader - (File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs)

### Summary
`read_felt_array` in the deprecated (Cairo0) syscall path takes the "array size" directly from an untrusted felt written into VM memory by the executing contract, converts it to `usize`, and immediately passes it to `felt_range_from_ptr`/`vm.get_integer_range` to size an allocation — with no upper bound check against the amount of memory that actually exists or is plausible. This is the same bug class as ALPINE-CVE-2021-32762 (hiredis/redis-cli): an attacker-influenced length value is used to size a heap allocation before any sanity/overflow check, which can trigger an oversized allocation attempt and crash/OOM the process parsing it — here, the sequencer's execution engine instead of a Redis client.

### Finding Description
`read_felt_array` reads a raw felt (`array_size`) from the Cairo VM, and if it is nonzero, converts it straight to `usize` and forwards it to `felt_range_from_ptr(vm, array_data_start_ptr, size)`, which calls `vm.get_integer_range(ptr, size)`: [1](#0-0) 

`felt_range_from_ptr` performs no bound validation before delegating to the Cairo VM: [2](#0-1) 

`array_size` here is not derived from any real, already-allocated pointer range — unlike the sibling implementation used by the current (Cairo1) syscall path, which derives its size from the difference between two `Relocatable` pointers that are guaranteed to correspond to actual VM segment offsets: [3](#0-2) 

and unlike `CairoArray::try_from`, which explicitly validates the declared item count against the number of remaining felts *before* calling `Vec::with_capacity`, specifically to prevent "a contract-controlled length felt from triggering an unbounded `Vec::with_capacity`": [4](#0-3) 

In the deprecated path, a Cairo0 contract's own compiled bytecode fully controls the felt value written at the "size" slot before invoking any syscall that reads a `(size, data_ptr)` pair through `read_calldata`/`read_call_params`/`read_felt_array` (e.g. `call_contract`, `library_call`, or message payload reads): [5](#0-4) 

Since Cairo0 execution places no implicit range-check on arbitrary felt values unless the contract's own code range-checks them, a contract author can write any felt (up to `Felt::MAX`, ~2^251) into that slot. `usize::try_from(array_size.to_biguint())` will succeed for any value representable in a 64-bit `usize` (i.e., up to `u64::MAX`), so a value like `2^40` or `2^63` passes the conversion and is handed to the Cairo VM as an allocation-size request.

### Impact Explanation
Any account can call a maliciously crafted, but validly declared/deployed, Cairo0 contract that sets a huge "size" felt before invoking a syscall that funnels through `read_felt_array`. This drives `vm.get_integer_range` to attempt to materialize a `Vec` sized to the attacker-chosen value (tens of GB to TB scale) on every node that executes/re-executes this transaction (block building, validation, and OS re-execution). This can crash the sequencer process (OOM) or stall it for extended periods while attempting the allocation, causing honest nodes processing the same transaction to fail or diverge in availability — a network-wide denial of transaction confirmation, matching the "network unable to confirm new transactions" acceptance criterion.

### Likelihood Explanation
Likelihood is high for the specific pattern: Cairo0 declaration/deployment is available to any unprivileged account, and the vulnerable code path (`read_felt_array`) is reached by common deprecated syscalls (`call_contract`, `library_call`, and similar) that any Cairo0 contract can invoke with attacker-chosen operand values. No special privileges, timing, or race conditions are required — a single crafted transaction against a self-controlled deployed contract is sufficient to reach the vulnerable allocation site.

### Recommendation
Add an explicit bound check in `read_felt_array` (deprecated_syscalls/hint_processor.rs) before allocating: validate `array_size` against a sane upper bound (e.g., existing segment size, remaining calldata length, or a hard protocol-defined maximum) prior to calling `felt_range_from_ptr`/`vm.get_integer_range`, mirroring the guard already present in `CairoArray::try_from` in `apollo_cairo_utils`. Alternatively, migrate the deprecated reader to derive size from validated pointer arithmetic the way the non-deprecated `read_felt_array` in `crates/blockifier/src/execution/syscalls/hint_processor.rs` does.

### Proof of Concept
1. Declare and deploy a Cairo0 (deprecated) contract whose compiled bytecode, before invoking `call_contract_syscall` or `library_call_syscall`, writes an attacker-chosen huge felt (e.g. `2^63 - 1`) into the memory slot that the syscall's `(calldata_size, calldata_ptr)` request structure reads.
2. Submit an ordinary INVOKE transaction that calls this contract's external entry point, triggering the syscall.
3. During execution, `read_call_params`/`read_calldata` → `read_felt_array` reads the crafted size felt, converts it to `usize`, and calls `felt_range_from_ptr(vm, ptr, huge_size)` → `vm.get_integer_range(ptr, huge_size)`, requesting an allocation sized to the attacker-chosen value, well before any real memory/segment bound is consulted.
4. Observe process memory exhaustion or a crash/hang on the executing node (and any node re-executing/validating the same block), preventing normal block processing. [1](#0-0) [2](#0-1)

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

**File:** crates/apollo_cairo_utils/src/lib.rs (L108-129)
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
```
