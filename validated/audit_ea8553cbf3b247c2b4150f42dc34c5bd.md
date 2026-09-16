### Title
Unvalidated Attacker-Controlled Array Length Passed to Memory Range Read Enables Memory-Consumption DoS in Cairo0 Syscall Argument Parsing - (File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs)

### Summary
The disclosed PyTorch bug is a length/size value taken from untrusted packed-sequence metadata and used to drive a memory read/allocation without validating it against the actual amount of backing data, causing uncontrolled memory consumption. The same bug class is reachable in the sequencer's execution of legacy (Cairo0) contract syscalls: `read_felt_array` reads an arbitrary, contract-supplied felt as the declared array length and immediately uses it to read/allocate a `Vec<Felt>` of that length, without bounding it against the amount of memory actually available at the pointed-to location.

### Finding Description
`read_felt_array` in the deprecated (Cairo0) syscall hint processor reads the "array size" directly as an arbitrary felt written by the executing contract's own bytecode, with no upper-bound or consistency check against the real contents of the array segment: [1](#0-0) 

This size is converted to `usize` and passed straight into `felt_range_from_ptr`, which calls `vm.get_integer_range(ptr, size)` and collects the result into a `Vec<Felt>`: [2](#0-1) 

This helper is invoked from multiple untrusted-input paths in Cairo0 syscall argument parsing, including `read_calldata` (used by `CallContract`/`LibraryCall`), `EmitEvent` (`keys`/`data`), and `SendMessageToL1` (`payload`): [3](#0-2) [4](#0-3) [5](#0-4) 

Unlike the modern (Cairo1) `read_felt_array`, which derives the size from the *difference between two pointers into the same segment* (`array_data_end_ptr - array_data_start_ptr`) — bounding it to values consistent with how the VM segment builder works: [6](#0-5) 

the deprecated version accepts a raw felt value as the length with no relationship to the actual data present, and no assertion (e.g., against a `SIERRA_ARRAY_LEN_BOUND`-style constant, which is only enforced separately for `calldata_size` in the OS's entry-point Cairo code) is applied to it before use: [7](#0-6) 

A malicious contract author can compile Cairo0 bytecode that writes an extremely large felt as the "array_size" argument to any of these syscalls (e.g., `emit_event`, `send_message_to_l1`, or a call/library-call's `calldata_len`) at negligible cost (this is just a memory store), then triggers the syscall. This causes every executing node (batcher/sequencer during block building, and every validator/full node re-executing the block or replaying the transaction, including the Starknet OS re-execution used for proving) to attempt to materialize a `Vec<Felt>` sized to the attacker-chosen value before any bounds check occurs.

### Impact Explanation
An attacker can declare a class containing a Cairo0 contract that abuses this pattern and then invoke it via a single ordinary transaction. Because the length check happens only implicitly (by later failing to read enough underlying memory), the allocation attempt (`Vec::with_capacity`-style sizing implied by collecting a sized range) occurs *before* the eventual out-of-bounds memory error is raised, so the process can be driven to allocate gigabytes of memory for a single felt array argument. This is a resource-exhaustion / crash vector on every honest node that executes or re-executes the transaction (batcher, all validators processing the same block, and OS re-execution used to build STARK proofs), which can degrade or halt block production/confirmation — matching the CWE-119 "memory consumption through length manipulation" class of the reference advisory.

### Likelihood Explanation
This is reachable from a single L2 transaction that declares and invokes a Cairo0 (deprecated) class — no privileged access, staking, or off-chain component is required. Cairo0 classes remain declarable/invokable in the current protocol, so the code path is live in production sequencer logic, not a dead/legacy-only path.

### Recommendation
- In `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::read_felt_array`, validate the declared `array_size` against a maximum bound (consistent with the existing `SIERRA_ARRAY_LEN_BOUND` enforced elsewhere for calldata) before calling `felt_range_from_ptr`, and reject with a syscall error if it is exceeded.
- Alternatively/additionally, change `felt_range_from_ptr` (`crates/blockifier/src/execution/execution_utils.rs:228`) to perform the memory read incrementally (or verify the target segment's actual used size) instead of eagerly requesting a range of attacker-declared length.
- Apply the same maximum-length validation uniformly to all `read_felt_array` call sites (`EmitEvent`, `SendMessageToL1`, `read_calldata`) rather than relying on the OS-level Cairo assertion that only covers top-level `calldata_size`.

### Proof of Concept
Conceptual PoC (cannot be executed without VM/test harness access):
1. Author and declare a Cairo0 class whose entry point, upon invocation, writes an arbitrarily large felt (e.g., close to `usize::MAX`) as the `data_len`/`keys_len` argument for an `emit_event` syscall (or `payload_size` for `send_message_to_l1`, or `calldata_len` for `call_contract`/`library_call`), followed by a data pointer to a small/empty segment.
2. Submit an ordinary `INVOKE` transaction that calls this entry point.
3. During execution, `EmitEventRequest::read` → `read_felt_array` (`crates/blockifier/src/execution/deprecated_syscalls/mod.rs:283` and `hint_processor.rs:939-949`) reads the declared huge size and calls `felt_range_from_ptr(vm, ptr, huge_size)`, which attempts to materialize a `Vec<Felt>` of that size on every node executing/re-executing this transaction, before the eventual bounds error is surfaced.

Note: I could not verify the exact allocation behavior of the external `cairo-vm` crate's `get_integer_range` (it is a third-party dependency not indexed in this repository), so I cannot confirm with certainty whether it allocates eagerly (worst case) or lazily per-element. The root-cause gap — the sequencer code passing an unvalidated, attacker-chosen length directly into a range-read helper — is confirmed within this repository's code, but the precise memory-consumption magnitude depends on that dependency's internal implementation, which is outside the indexed codebase.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/mod.rs (L277-290)
```rust
impl SyscallRequest for EmitEventRequest {
    // The Cairo struct contains: `keys_len`, `keys`, `data_len`, `data`·
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> DeprecatedSyscallExecutorBaseResult<EmitEventRequest> {
        let keys = read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?
            .into_iter()
            .map(EventKey)
            .collect();
        let data = EventData(read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?);

        Ok(EmitEventRequest { content: EventContent { keys, data } })
    }
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/mod.rs (L424-436)
```rust
impl SyscallRequest for SendMessageToL1Request {
    // The Cairo struct contains: `to_address`, `payload_size`, `payload`.
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> DeprecatedSyscallExecutorBaseResult<SendMessageToL1Request> {
        let to_address_felt = felt_from_ptr(vm, ptr)?;
        let to_address = to_address_felt.into();
        let payload =
            L2ToL1Payload(read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?);

        Ok(SendMessageToL1Request { message: MessageToL1 { to_address, payload } })
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L220-224)
```text
    // Sanity check: Verify that `calldata` is a valid Sierra array.
    // Don't use `assert_nn_le` for efficiency.
    assert [range_check_ptr] = calldata_size;
    assert [range_check_ptr + 1] = calldata_size + 2 ** 128 - SIERRA_ARRAY_LEN_BOUND;
    let range_check_ptr = range_check_ptr + 2;
```
