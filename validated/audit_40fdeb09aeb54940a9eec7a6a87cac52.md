### Title
Deprecated (Cairo0) syscall array reader trusts an unvalidated length felt for allocation before validating memory bounds — pre-completion resource exhaustion - (File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs)

### Summary
The advisory describes an "unbounded chunk size" that is trusted for allocation before authentication/validation, causing pre-auth resource exhaustion. The closest reachable analog in the sequencer is `read_felt_array` in the deprecated (Cairo0) syscall handling path, which reads an array-length felt directly out of contract-controlled VM memory and passes it straight into an allocation-sized read, instead of deriving the size from validated start/end pointers as the non-deprecated (Cairo1) implementation does.

### Finding Description
`read_felt_array` for deprecated syscalls reads `array_size` as a raw felt value supplied by the executing (attacker-deployable) Cairo0 contract, and — apart from a zero-length short-circuit — immediately converts it to `usize` and forwards it to `felt_range_from_ptr` to build the array: [1](#0-0) 

This is structurally different from — and weaker than — the Cairo1 (non-deprecated) implementation, which computes the array size as the difference between an explicit `array_data_end_ptr` and `array_data_start_ptr`, i.e., the size is derived from two pointers that must both already resolve in VM memory, rather than from an arbitrary attacker-chosen integer: [2](#0-1) 

`read_felt_array` (deprecated variant) is used to parse `Calldata` for deprecated syscall requests such as `call_contract`/`library_call`/`delegate_call` parameter parsing: [3](#0-2) 

Because `array_size` here is an attacker-controlled felt (bounded only by the field prime, ~2^251, and truncated to `usize` on conversion), any Cairo0 contract entry point that issues a syscall using this parser can request an allocation/range read for up to `usize::MAX` elements. Whether this manifests as an immediate large allocation depends on the downstream `felt_range_from_ptr`/VM range-read implementation (in `crates/blockifier/src/execution/execution_utils.rs`, which I was not able to fully inspect before running out of tool budget); if that function performs a `Vec::with_capacity(size)`-style allocation ahead of per-cell existence checks (a common pattern in Cairo-VM range readers), the parser would attempt an outsized allocation using a value that has not been validated against any actual backing memory — the same "trust a length field before validating it against real data" root cause as the reported Bouncy Castle AEAD chunk-size bug.

### Impact Explanation
If the length is used for allocation before being checked against the real amount of available data, an attacker can deploy and invoke a Cairo0 contract whose entry point issues a syscall (e.g., `call_contract`) with an oversized bogus calldata-array-size felt. Every node executing that transaction (validators/sequencers re-executing it, or the Starknet OS re-executing the block) would attempt the same outsized allocation, potentially crashing or exhausting memory on the node process handling execution — a network-wide denial of service reachable from a single submitted transaction, matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Likelihood is uncertain because I could not fully confirm, within the available tool budget, whether `felt_range_from_ptr`/the underlying VM range-read function performs the allocation before validating memory bounds, or whether it validates cell-by-cell without a large up-front allocation (in which case the impact would be limited to a normal out-of-range error rather than a resource-exhaustion crash). The size difference between the deprecated and non-deprecated `read_felt_array` implementations (trusted length felt vs. pointer-difference) is a genuine, verifiable difference in this repo, but the ultimate severity hinges on internal `cairo-vm` allocation behavior that I was unable to trace to completion.

### Recommendation
- Verify whether `felt_range_from_ptr` (and the underlying VM range/continuous-read function it calls) allocates a buffer sized by the untrusted `array_size` before validating memory bounds; if so, bound-check `array_size` against a sane maximum (e.g., max calldata/segment size) or against the actual number of populated cells before allocating.
- Align the deprecated `read_felt_array` with the non-deprecated implementation's approach of deriving array size from validated start/end pointers rather than trusting an arbitrary length felt.
- Add a fuzz/regression test that supplies a very large `array_size` felt to a deprecated syscall's calldata parser and asserts the transaction is rejected cheaply (bounded resource use) rather than triggering a large allocation attempt.

### Proof of Concept
Conceptual (not fully verified end-to-end due to inability to inspect `felt_range_from_ptr`'s internals in this session):
1. Deploy a Cairo0 (deprecated) contract whose entry point issues a syscall (e.g., `call_contract`) with its calldata `array_size` word set to a very large felt value (e.g., close to `usize::MAX` after truncation) instead of the real calldata length, while the actual backing memory segment for `array_data_start_ptr` contains few or no populated cells.
2. Submit an invoke transaction that calls this entry point.
3. Observe whether the node's execution/blockifier or OS re-execution path attempts to allocate/read a buffer of the declared (bogus) size before validating it against the real segment contents, leading to abnormal memory consumption or a crash during transaction execution — as opposed to failing fast with a bounded-cost "out of memory range" error.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L931-951)
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
