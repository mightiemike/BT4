### Title
Unbounded memory allocation from attacker-controlled array-size felt in deprecated (Cairo0) syscall argument parsing - ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
The Cairo0 ("deprecated") syscall argument parser reads an array length directly as a raw felt supplied in VM memory by the executing contract, converts it to `usize`, and immediately passes it to allocate a `Vec<Felt>` of that size, before any bound has been enforced against it. Any contract invoking `call_contract`, `library_call`, `delegate_call`, or `deploy` with a maliciously large size field can push the sequencer to attempt an allocation up to `usize::MAX` elements, matching the same bug class as CVE-2026-42005 ("insufficient input validation ... causes unlimited memory allocation ... leading to a denial of service").

### Finding Description
`read_felt_array` in the deprecated (Cairo0) syscall hint processor reads the array-size felt straight from syscall-request memory and converts it to a native integer without any upper bound check, then uses it to allocate/read a felt range: [1](#0-0) 

Contrast this with the newer (Cairo1) syscall reader in `crates/blockifier/src/execution/syscalls/hint_processor.rs`, whose `read_felt_array` derives the array size from the *difference between two pointers already present in a real, allocated VM memory segment* rather than from an arbitrary felt value: [2](#0-1) 

The deprecated variant instead trusts a bare felt (`array_size`) taken from the syscall request struct written by the executing Cairo0 bytecode (e.g. `calldata_size` in `call_contract`/`library_call`/`deploy` requests). Because Cairo0 contracts write raw felts into memory as part of their compiled instructions, a malicious (but validly declared/deployed) Cairo0 contract fully controls this value and can set it to an arbitrarily large felt. `usize::try_from(array_size.to_biguint())` will succeed for any value up to `usize::MAX` (2^64-1 on 64-bit hosts), and the resulting `felt_range_from_ptr` call then attempts to allocate/copy a `Vec<Felt>` of that size — an allocation request large enough to abort the process (Rust's allocator aborts on OOM rather than gracefully failing) before the missing-memory error from cairo-vm would ever be reached.

This is analogous to the reported PowerDNS bug: a size value taken from untrusted input is used to size a memory allocation without a sanity bound, and the internal component performing the allocation (here, the sequencer's execution engine rather than a web server) can be driven to unbounded memory use.

Note: I could not directly inspect the body of `felt_range_from_ptr` (defined in `crates/blockifier/src/execution/execution_utils.rs`) within the available context to confirm the exact allocation call it performs; this assessment is based on the standard cairo-vm `get_continuous_range`-style pattern that pre-allocates a `Vec` sized by the caller-supplied length before validating memory bounds. This should be verified directly in a full checkout.

### Impact Explanation
If confirmed, a single attacker-controlled or attacker-invoked Cairo0 contract call can force the sequencer process (during transaction execution, not just gateway validation) to attempt a massive allocation, crashing or hanging the executing sequencer node. Because block building/execution runs on every sequencer that must apply the same transaction, this could be replayed against every honest node processing the block, resulting in denial of service to the network's ability to confirm new transactions — satisfying the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Reachability requires only an ordinary invoke transaction targeting a deployed Cairo0 contract that issues one of the affected syscalls (`call_contract`, `library_call`, `delegate_call`, `deploy`) with a crafted large size felt written into the request memory — no special privileges, staking, or operator/proposer role needed. Cairo0 classes remain declarable/deployable and invocable on Starknet, so the entry point is reachable by any unprivileged transaction sender.

### Recommendation
Add an explicit upper bound check (e.g., against `SIERRA_ARRAY_LEN_BOUND` or the existing `max_calldata_length`/similar gas-tied limits) on `array_size` in `read_felt_array` (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`) before converting it to `usize` and before any allocation occurs, mirroring the bound check already present in the Cairo OS hints (`assert_nn_le(..., SIERRA_ARRAY_LEN_BOUND - 1)`) and the defensive check recently added to `apollo_cairo_utils`'s `CairoArray::try_from` (validating the declared length against remaining available data before calling `Vec::with_capacity`): [3](#0-2) 

### Proof of Concept
1. Declare/deploy a Cairo0 contract whose compiled bytecode directly writes an oversized felt (e.g. close to `Felt::MAX`) into the `calldata_size`/array-size slot of a `call_contract`, `library_call`, `delegate_call`, or `deploy` syscall request before invoking the syscall.
2. Submit an ordinary `INVOKE` transaction that triggers execution of this contract's entry point.
3. During execution, `SyscallHintProcessor`/`DeprecatedSyscallExecutor` reaches `read_felt_array` (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:931-950`), converts the oversized felt to `usize`, and calls `felt_range_from_ptr` with that size, triggering an oversized allocation attempt in the sequencer process handling the transaction.

### Citations

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

**File:** crates/apollo_cairo_utils/src/lib.rs (L118-130)
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

```
