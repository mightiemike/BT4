## Analysis Result

### Title
Unbounded syscall array length causes memory-exhaustion DoS during transaction execution - ([File: crates/blockifier/src/execution/execution_utils.rs])

### Summary
The Django formset bug (CVE-2013-0306) let an attacker submit an unvalidated `max_num` value that was used to size internal data structures before any sanity check, causing unbounded memory consumption. The sequencer's `blockifier` syscall-argument readers have the same shape of bug: a syscall's calldata length is read directly out of the calling contract's own VM memory as an untrusted `Felt`/pointer-difference, converted to a `usize`, and passed straight into `Vec`-allocating helpers with no upper bound check before the allocation happens.

### Finding Description
For legacy (Cairo0) contracts, `read_felt_array` reads an attacker-controlled `array_size` felt directly from VM memory (written by the executing contract itself when it builds a syscall request such as `library_call`/`call_contract`), converts it to a `usize`, and immediately calls `felt_range_from_ptr` with that size: [1](#0-0) 

`felt_range_from_ptr` then calls `vm.get_integer_range(ptr, size)` with the fully attacker-controlled `size`, with no cap: [2](#0-1) 

The Cairo1 syscall path has an analogous pattern: `array_size` is derived from the difference of two pointers that the executing contract itself writes into its own memory segment, and is likewise passed unchecked into `felt_range_from_ptr`: [3](#0-2) 

Neither path validates the declared length against the transaction's `max_calldata_length` (enforced only once, at the gateway, on the outer transaction's calldata) or against the remaining bytes actually available in the segment before allocating. This is the exact bug class described in the report: an attacker-supplied count is used to size an allocation before being validated against the data that is actually present. Notably, the codebase elsewhere (`apollo_cairo_utils/src/lib.rs`) explicitly guards against this same pattern for `Retdata` parsing by checking the declared length against `iter.len()` before calling `Vec::with_capacity`: [4](#0-3) 

...but the syscall-argument readers in `deprecated_syscalls/hint_processor.rs` and `syscalls/hint_processor.rs` lack the equivalent guard.

### Impact Explanation
A contract deployer (any unprivileged account can declare/deploy a Cairo0 or Cairo1 class) can invoke `library_call`, `call_contract`, or any other syscall that reads a calldata array, and set the declared array length to an arbitrarily large value in its own memory. This causes the executing blockifier instance — proposer, every honest validator re-executing the block, and any full node re-executing the block for RPC/indexing — to attempt a huge `Vec<Felt>` allocation before any bytecode step budget for the inner call is charged. On a 64-bit system this can request tens or hundreds of gigabytes for a single felt value, aborting or OOM-killing the process. Because every honest node executing the same block hits this identically, it is not just a single-node crash but a potential network-wide inability to process the block containing the malicious transaction, until the vulnerable code is patched — a network unable to confirm new transactions.

### Likelihood Explanation
The path is reachable via a single ordinary transaction: declare a small class using `library_call`/`call_contract` with a crafted length felt, then invoke it. No special privileges, staking, or node compromise is required — it only requires the ability to submit a transaction and get it included, which is the baseline threat model for CWE-400 in this bug class.

### Recommendation
Cap the declared syscall array/calldata length against a sane upper bound (e.g., the same `max_calldata_length`/Sierra array length bound already used at the entry-point level) before calling `felt_range_from_ptr`/`vm.get_integer_range`, mirroring the guard already used in `apollo_cairo_utils::TryFrom<Retdata> for CairoArray<T>` (validate declared length against actually-addressable memory/segment size and a hard cap before allocating).

### Proof of Concept
1. Declare and deploy a minimal Cairo0 (or Cairo1) contract whose entry point issues a `library_call` (or `call_contract`) syscall.
2. In the contract's own memory, set the syscall request's calldata-length field (`calldata_size`, or the Cairo1 end-pointer offset) to a very large value (e.g., `10^10`), while leaving the actual backing data absent/small.
3. Submit an `INVOKE` transaction calling this entry point.
4. During execution, `read_felt_array` → `felt_range_from_ptr` → `vm.get_integer_range` attempts to allocate a `Vec<Felt>` sized to the attacker-chosen length before validating that the data exists, causing excessive memory allocation/OOM in every node that executes the transaction (proposer and all re-executing validators/full nodes).

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

**File:** crates/apollo_cairo_utils/src/lib.rs (L111-130)
```rust
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
