### Title
Unbounded array-size felt used directly as allocation size in deprecated (Cairo 0) syscall calldata parsing enables memory-allocation-failure DoS - ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
`read_felt_array` in the deprecated (Cairo 0) syscall path reads a raw, attacker-controlled felt from Cairo VM memory as an array length and, after only a `usize::try_from` conversion, passes it directly as the allocation `size` to `felt_range_from_ptr` → `vm.get_integer_range(ptr, size)`, without ever checking it against the amount of data actually available or any sane upper bound. This mirrors the libzip CVE-2017-14107 pattern: an untrusted length field taken from input data drives a memory allocation before the input is validated to actually contain that much data, causing a memory-allocation failure.

### Finding Description
`read_felt_array` (used by `read_calldata`/`read_call_params`, which parse the calldata for deprecated `call_contract`, `library_call`, `delegate_call`, etc. syscalls issued by a Cairo 0 contract) does: [1](#0-0) 

```
let array_size = felt_from_ptr(vm, ptr)?;
...
Ok(felt_range_from_ptr(vm, array_data_start_ptr, usize::try_from(array_size.to_biguint())?)?)
```

`array_size` is read straight from VM memory with `felt_from_ptr`, which just dereferences the pointer with no bound or plausibility check: [2](#0-1) 

It is then handed unmodified (other than a felt→usize cast, which on a 64-bit sequencer succeeds for any value up to `usize::MAX`, i.e. up to 2^64-1) to `felt_range_from_ptr`: [3](#0-2) 

```
pub fn felt_range_from_ptr(vm: &VirtualMachine, ptr: Relocatable, size: usize) -> Result<Vec<Felt>, VirtualMachineError> {
    let values = vm.get_integer_range(ptr, size)?;
    ...
}
```

`vm.get_integer_range` is implemented in the external `cairo-vm` crate and allocates a `Vec` sized according to the requested `size` before validating that the memory segment actually contains that many elements. Because `array_size` here is fully attacker-controlled (a Cairo 0 contract writes this value itself into its own memory before invoking the syscall — there is no upper bound check anywhere in `read_felt_array`, unlike the sibling Cairo-1 implementation which derives the size safely from `array_data_end_ptr - array_data_start_ptr` within a single segment): [4](#0-3) 

A malicious contract deployer can declare and deploy a trivial Cairo 0 contract whose `__execute__`/entrypoint issues a `call_contract` (or `library_call`/`delegate_call`) syscall while writing an absurdly large value (e.g., close to `usize::MAX`) as the "calldata_size" felt at the location `read_calldata`/`read_call_params` expects. When any node (sequencer building/validating the block or a full node/OS re-executing it) executes this transaction, `read_felt_array` will attempt to allocate a `Vec<Felt>` (32 bytes per element) sized by that value, causing an out-of-memory abort/crash of the executing process — exactly the "memory allocation failure" bug class described in CVE-2017-14107 for `_zip_read_eocd64`/`_zip_cdir_grow`, where an untrusted length field is used to size an allocation before validating the underlying data actually supports that size.

### Impact Explanation
An attacker who can submit a single transaction (deploy + invoke, or simply declare+invoke against an already-deployed contract) can trigger an uncontrolled, attacker-chosen memory allocation deep inside blockifier's deprecated-syscall calldata parsing. Since this code runs both during sequencer transaction execution/block building and during full-node/Starknet-OS re-execution, a successful trigger can crash the executing process (allocator abort / OOM-killed process), causing denial of service on any node that executes the malicious transaction, i.e., the network becoming unable to confirm new transactions on affected nodes — matching the "network unable to confirm new transactions" impact criterion.

### Likelihood Explanation
Likelihood is high for any chain that still permits Cairo 0 (`ContractClass::V0`, deprecated syscalls) declare/deploy/invoke flows, since:
- The attacker needs only standard, unprivileged capabilities: declare a Cairo 0 class, deploy it, and invoke it with crafted memory contents (fully under the caller's control, since it's the contract's own execution memory).
- No special permissions, staking, or privileged role required — an ordinary transaction sender/contract deployer suffices.
- There is no existing bound check on `array_size` in `read_felt_array` for the deprecated syscall path prior to this allocation.

### Recommendation
In `read_felt_array` (crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs), validate `array_size` against a sane upper bound (e.g., the same reasoning used in the Cairo-1 `read_felt_array`, which computes size from `array_data_end_ptr - array_data_start_ptr` within a single memory segment) before calling `felt_range_from_ptr`. At minimum, reject sizes that exceed the currently-used size of the segment referenced by `array_data_start_ptr` (which can be queried via the VM before allocating), so that the requested length can never exceed the amount of memory the contract has actually populated, preventing arbitrary-size allocation from a single crafted felt.

### Proof of Concept
1. Declare and deploy a Cairo 0 contract whose entrypoint constructs a `call_contract` (or `library_call`) syscall request in its own segment, setting:
   - `function_selector` = any valid felt,
   - `calldata_size` = a large felt value (e.g., `2**62`), which converts successfully via `usize::try_from`,
   - `calldata` pointer = any valid relocatable (the actual backing segment can be tiny or empty).
2. Submit an `invoke` transaction calling this entrypoint.
3. When the sequencer/full node executes the transaction, `read_calldata` → `read_felt_array` reads `calldata_size` as `2**62` and calls `felt_range_from_ptr(vm, calldata_ptr, 2**62)`, which attempts `vm.get_integer_range` sized to `2**62` felts (≈ 2^62 * 32 bytes), triggering an allocation far beyond available memory and aborting/crashing the executing process before any bytecode-based gas metering can stop it.

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

**File:** crates/blockifier/src/execution/execution_utils.rs (L210-217)
```rust
pub fn felt_from_ptr(
    vm: &VirtualMachine,
    ptr: &mut Relocatable,
) -> Result<Felt, VirtualMachineError> {
    let felt = vm.get_integer(*ptr)?.into_owned();
    *ptr = (*ptr + 1)?;
    Ok(felt)
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
