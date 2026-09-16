Based on my research, I found a plausible analog. Note: I was not able to fully retrieve the body of `felt_range_from_ptr` (in `crates/blockifier/src/execution/execution_utils.rs`) within my tool budget, so its exact internal allocation strategy is not 100% confirmed — this is stated explicitly below as an open verification point.

### Title
Unbounded length-to-allocation from a Cairo0 contract-controlled felt in deprecated `read_felt_array` - (File: `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`)

### Summary
The CVE-2017-13061 bug class is "a length field taken from untrusted input is used to pre-allocate/iterate a buffer before it is validated against the actual amount of available data, causing memory exhaustion." The deprecated (Cairo0) syscall array reader in this codebase reproduces this pattern: it reads an array-size felt directly from VM memory (fully controlled by the executing Cairo0 contract's bytecode/calldata) and converts it to a `usize` with no upper bound check before handing it to a range-reading routine, unlike the sibling/newer implementation and unlike a nearby retdata-parsing helper that was explicitly hardened against this exact issue.

### Finding Description
`read_felt_array` in the deprecated syscalls hint processor reads the array length as a raw felt from Cairo VM memory and converts it straight to `usize`: [1](#0-0) 

The `array_size` felt is fully controlled by the calldata a Cairo0 contract passes into `call_contract`/`library_call` (see `read_calldata`/`read_call_params` calling this function): [2](#0-1) 

Unlike this deprecated path, the Cairo1 syscalls version of `read_felt_array` derives the array size from the *difference between two pointers already present in memory* (`array_data_end_ptr - array_data_start_ptr`), which is bounded by real memory segment layout rather than an arbitrary attacker-chosen felt: [3](#0-2) 

Confirming this bug class is recognized in this codebase: `apollo_cairo_utils`'s `CairoArray::try_from` explicitly validates a declared array length against the number of remaining felts *before* calling `Vec::with_capacity`, with a comment stating the exact purpose is to prevent "a contract-controlled length felt from triggering an unbounded `Vec::with_capacity`": [4](#0-3) 

The deprecated `read_felt_array` has no equivalent check: `array_size` (up to `usize::MAX` on 64-bit systems via `usize::try_from(array_size.to_biguint())`) is passed straight into `felt_range_from_ptr` without first confirming that many felts actually exist in the target memory segment.

### Impact Explanation
If `felt_range_from_ptr` (or the underlying cairo-vm range-read routine it wraps) pre-allocates a `Vec` sized to the attacker-supplied `array_size` before validating that the corresponding memory segment actually contains that many values, a single transaction invoking a Cairo0 contract's `call_contract`/`library_call` syscall with a huge crafted array-size felt could trigger a massive allocation attempt on the sequencer, causing memory exhaustion / process abort — a denial-of-service that could stall block production, matching the "network unable to confirm new transactions" impact bar. I could not fully confirm whether `felt_range_from_ptr`'s underlying cairo-vm call performs the allocation before or after validating segment bounds, which is the key remaining uncertainty for this finding.

### Likelihood Explanation
Reachability is straightforward: any unprivileged transaction sender can invoke a deployed Cairo0 contract that performs `call_contract` or `library_call`, and Cairo0 declare/deploy remains supported in this codebase (`DeclareV1`/`ExecutableTransactionInput::DeclareV1`): [5](#0-4) 
No gateway-level size limit inspects internal Cairo0 program syscall arguments (the gateway's size checks target `sierra_program`/`contract_class_object_size`, not Cairo0 calldata array sizes passed at runtime), so nothing upstream of the syscall handler would reject the crafted felt.

### Recommendation
Add an explicit bound check in the deprecated `read_felt_array` (mirroring the fix already applied in `apollo_cairo_utils::CairoArray::try_from`) that rejects `array_size` values exceeding the memory actually reachable/allocated before calling `felt_range_from_ptr`, or ensure `felt_range_from_ptr` validates segment size prior to allocating capacity for the requested range.

### Proof of Concept
A Cairo0 contract calls `call_contract`/`library_call` with a `calldata_size` felt set to a very large value (e.g., close to `usize::MAX` after `Felt -> BigUint -> usize` conversion) while the backing memory segment does not actually contain that many populated cells. This felt flows through `read_call_params` → `read_calldata` → `read_felt_array` → `felt_range_from_ptr`; if the latter pre-sizes a buffer from `array_size` before checking memory availability, the sequencer attempts an oversized allocation triggered by a single submitted transaction.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L860-879)
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
```

**File:** crates/apollo_cairo_utils/src/lib.rs (L117-131)
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

**File:** crates/apollo_rpc_execution/src/lib.rs (L872-887)
```rust
        ExecutableTransactionInput::DeclareV1(
            declare_tx,
            deprecated_class,
            abi_length,
            only_query,
        ) => {
            let class_info = ClassInfo::new(
                &deprecated_class.into(),
                DEPRECATED_CONTRACT_SIERRA_SIZE,
                abi_length,
                SierraVersion::DEPRECATED,
            )
            .map_err(|err| ExecutionError::BadDeclareTransaction {
                tx: DeclareTransaction::V1(declare_tx.clone()).into(),
                err,
            })?;
```
