### Title
Unbounded memory allocation from attacker-controlled array-size felt in deprecated (Cairo0) syscall argument parsing - ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
The Cairo0 ("deprecated") syscall request-parsing path reads an array length directly from a felt value stored in Cairo VM memory and immediately uses it to compute a read range that is materialized into a `Vec<Felt>`, without validating that the claimed length is plausible relative to the calling contract's actual memory/resources, mirroring the CVE-2021-3479 pattern of trusting an attacker-influenced size field before bounding the resulting allocation.

### Finding Description
`read_felt_array` in the deprecated syscall hint processor reads an `array_size` felt straight from VM memory, converts it to `usize`, and passes it to `felt_range_from_ptr`, which calls `vm.get_integer_range(ptr, size)` to build a `Vec<Felt>` of that length: [1](#0-0) 

The only guard is the zero-check for an empty array; any nonzero value that fits into `usize` is accepted and forwarded to allocate a Felt range: [2](#0-1) 

This function backs `read_calldata`, which is used to parse the `CallContract`, `DelegateCall`, `LibraryCall`, and `Deploy` deprecated syscall requests: [3](#0-2) 

Because these Cairo0 syscall structs are populated by raw memory writes performed by the executing Cairo0 bytecode (or via hints), a contract author can write an arbitrarily large `calldata_size`/array-size felt into the syscall segment without providing a matching amount of real backing data — this is explicitly demonstrated by existing test contract code that manually asserts malformed syscall fields (e.g., `assert syscall_ptr[3] = 1` while supplying no real payload) to exercise the syscall-parsing path: [4](#0-3) 

I was not able to fully confirm, within the code available to me, whether `vm.get_integer_range` (from the external `cairo-vm` crate) validates the segment's actual populated length before allocating a `Vec` of `size` elements, or whether it pre-allocates (e.g., via `Vec::with_capacity(size)`) before verifying memory bounds — the function body lives outside this repository's indexed sources. If it pre-allocates before bounds-checking, a single crafted felt value (e.g., close to `usize::MAX` on the felt-to-usize conversion boundary) would trigger a very large or failing allocation attempt purely from parsing syscall arguments, before any per-element read failure could be raised. This is the same root-cause shape as CVE-2021-3479: a length field taken from untrusted/crafted input is used to size an allocation without an upper bound check tied to actual available data or a hard cap.

### Impact Explanation
If reachable, this allows any transaction sender who can trigger execution of a Cairo0 contract (which remains supported by the deprecated syscall path in this codebase) to cause the executing node to attempt an excessive memory allocation during syscall argument parsing. This would manifest as increased memory pressure, allocator failure/abort, or process termination on every node that executes the transaction — a resource-exhaustion/availability impact on the sequencer/replaying full nodes, potentially affecting the network's ability to process the block if it triggers node crashes during consensus/re-execution.

### Likelihood Explanation
Exploitability depends on two unverified conditions I could not resolve with the tools available: (1) whether Cairo0 class declaration/execution is still permitted on the live network for new/attacker-authored contracts (vs. only pre-existing classes), and (2) the internal allocation behavior of `cairo_vm::VirtualMachine::get_integer_range`, which is outside this repository. Without confirming these, I cannot assert this is definitely exploitable end-to-end; it should be treated as a plausible bug-class analog requiring verification against the actual `cairo-vm` dependency version in use.

### Recommendation
Bound the parsed `array_size` in `read_felt_array` (both the deprecated and current syscall variants) against a hard maximum consistent with the maximum calldata/segment size allowed by protocol constants, and/or validate that the claimed size does not exceed the number of felts actually present in the target segment before allocating, similar to the defensive check already applied in `apollo_cairo_utils`'s retdata array parsing: [5](#0-4) 

### Proof of Concept
A Cairo0 contract entry point can bypass the compiler-generated length/pointer consistency checks by directly writing values into the `syscall_ptr` segment via a Cairo hint or inline `assert`, setting the array-size felt (e.g., the `calldata_size` field of `CallContract`/`Deploy`) to a very large value while providing no corresponding data segment, then invoking the syscall — as already exercised (for a different assertion) by `test_bad_syscall_request_arg_type` in the test contract: [4](#0-3) 
This would route into `read_felt_array` → `felt_range_from_ptr` → `vm.get_integer_range` with the attacker-chosen size. I could not execute this PoC or inspect the `cairo-vm` internals to confirm the allocation actually occurs before a bounds error is raised, so this should be validated with a live build against the exact `cairo-vm` version pinned in this repository before treating it as confirmed.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/mod.rs (L234-251)
```rust
impl SyscallRequest for DeployRequest {
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> DeprecatedSyscallExecutorBaseResult<DeployRequest> {
        let class_hash = ClassHash(felt_from_ptr(vm, ptr)?);
        let contract_address_salt = ContractAddressSalt(felt_from_ptr(vm, ptr)?);
        let constructor_calldata = read_calldata(vm, ptr)?;
        let deploy_from_zero = felt_from_ptr(vm, ptr)?;

        Ok(DeployRequest {
            class_hash,
            contract_address_salt,
            constructor_calldata,
            deploy_from_zero: felt_to_bool(deploy_from_zero)?,
        })
    }
}
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo0/security_tests_contract.cairo (L229-242)
```text
@external
func test_bad_syscall_request_arg_type{syscall_ptr: felt*}() {
    assert syscall_ptr[0] = CALL_CONTRACT_SELECTOR;
    // Contract address.
    assert syscall_ptr[1] = 0;
    // Function selector.
    assert syscall_ptr[2] = 0;
    // Calldata size.
    assert syscall_ptr[3] = 1;
    // Calldata - should be a pointer, but we are passing a felt.
    assert syscall_ptr[4] = 0;
    %{ syscall_handler.call_contract(segments=segments, syscall_ptr=ids.syscall_ptr) %}
    return ();
}
```

**File:** crates/apollo_cairo_utils/src/lib.rs (L118-131)
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
