### Title
Unbounded array-length felt from Cairo0 syscall arguments allows attacker-controlled memory exhaustion - ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
The CVE describes a Wireshark dissector that trusted an attacker-controlled length field without validating it, leading to a crash or memory exhaustion. The analogous pattern exists in the sequencer's Cairo0 ("deprecated") syscall handling: an array-length value that originates from a felt written by a Cairo0 contract's own bytecode is converted directly to `usize` and used to size a subsequent memory read/allocation, with no sanity bound applied before that conversion.

### Finding Description
`read_felt_array` in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:931-949` reads an `array_size` felt straight off the VM at the current pointer: [1](#0-0) 

It only special-cases `array_size == Felt::ZERO`; for any non-zero value it does `usize::try_from(array_size.to_biguint())` and immediately forwards that raw, attacker-influenced size into `felt_range_from_ptr`, which reads/allocates that many felts. There is no upper bound check against the actual size of the calldata segment or any configured maximum before the conversion/allocation is attempted. This function backs `read_calldata`/`read_call_params`, which are used by Cairo0 syscalls such as `call_contract`, `library_call`, and `deploy` (see usages in `crates/blockifier/src/execution/deprecated_syscalls/mod.rs`), meaning the `array_size` value is effectively whatever a Cairo0 (legacy) contract's bytecode places into the syscall request structure for its own calldata length argument.

This differs from the newer (non-deprecated) syscall path in `crates/blockifier/src/execution/syscalls/hint_processor.rs:860-880`, where the array size is derived from the *difference between two segment pointers* (`array_data_end_ptr - array_data_start_ptr`) rather than an arbitrary felt value — a stronger invariant that ties the "length" to something structurally related to actual memory layout. [2](#0-1) 

Because a Cairo0 class is user-declarable (a contract deployer/class declarer controls the bytecode that populates the deprecated syscall request), an attacker can declare and later invoke a Cairo0 contract that issues a `call_contract`/`library_call`/`deploy` syscall with a maliciously large `calldata_size` felt (e.g., close to `2^128`), causing the sequencer to attempt to allocate/read an enormous number of felts while executing that single transaction.

### Impact Explanation
If reachable, this causes the node executing the transaction (mempool validation / blockifier execution / block building) to attempt an unbounded or extremely large memory allocation, which can crash the executing process (OOM) or stall it, denying the network's ability to process further transactions until recovery — a network-availability impact matching the "network unable to confirm new transactions" category in scope.

### Likelihood Explanation
I could not fully verify two important preconditions with the available tool budget:
1. The exact allocation strategy of `felt_range_from_ptr` (i.e., whether it eagerly allocates a `Vec` of the requested capacity before validating memory bounds, or lazily reads and fails fast on the first missing cell). This determines whether the attack causes an actual large allocation or merely a fast `MemoryError`.
2. Whether gas/step metering in the VM would exhaust the transaction's resources before the conversion/allocation is reached, since `usize::try_from` and any subsequent allocation happen inside a syscall hint that executes after gas accounting for the syscall's *base* cost, not proportional to the claimed array size.

Given this uncertainty, likelihood cannot be confirmed as high without further investigation of `felt_range_from_ptr`'s implementation and the surrounding gas-charging order — this should be validated directly against the source before treating this as a confirmed, exploitable Medium/High finding.

### Recommendation
- In `read_felt_array` (deprecated_syscalls/hint_processor.rs), bound-check `array_size` against a reasonable maximum (e.g., the transaction's configured max calldata/array length) before converting to `usize` and before any allocation/read attempt.
- Prefer deriving array sizes from validated pointer arithmetic (as already done in the non-deprecated syscall path) rather than trusting a raw felt value supplied by contract bytecode.
- Audit `felt_range_from_ptr` to confirm it does not eagerly pre-allocate based on an unvalidated size argument.

### Proof of Concept
Conceptual PoC (not verified end-to-end due to tool limits):
1. Declare a Cairo0 class whose bytecode, prior to invoking `library_call_syscall` or `call_contract_syscall`, writes a `LibraryCallRequest`/`CallContractRequest` with `calldata_size` set to a very large felt (e.g., `2^100`) instead of the real calldata length.
2. Deploy and invoke this contract from a normal `INVOKE` transaction.
3. During execution, `read_calldata` → `read_felt_array` converts the felt to `usize` and calls `felt_range_from_ptr` with that size, potentially triggering a large allocation/attempted memory read on the executing sequencer node. [3](#0-2)

### Citations

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
