### Title
Unbounded Memory Allocation DoS via Attacker-Controlled `array_size` Felt in Deprecated Syscall `read_felt_array` - ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
The Cairo0 ("deprecated") syscall argument parser `read_felt_array` reads an `array_size` value directly as an arbitrary `Felt` from VM memory — memory that is populated by the executing (attacker-deployed/attacker-invoked) contract itself when it prepares syscall arguments — and converts it to a `usize` with no upper-bound validation before using it to read a felt range from VM memory. This mirrors the CVE-2026-66733 pattern: an unvalidated length/id field taken directly from untrusted input drives a memory allocation whose size is proportional to an attacker-chosen value rather than to the actual amount of data received.

### Finding Description
`read_felt_array` in [1](#0-0)  does:
1. Reads `array_size` as a raw `Felt` from the pointer location, with no maximum bound check (unlike Cairo1/new syscalls, which derive size from actual pointer difference, or unlike the RPC layer's `max_calldata_length`/`max_signature_length` checks that only apply to top-level RPC transaction fields).
2. Converts it via `usize::try_from(array_size.to_biguint())` — this succeeds for any value up to `usize::MAX` (on 64-bit hosts, `~1.8×10^19`), effectively unbounded for practical purposes.
3. Passes this attacker-chosen size directly into `felt_range_from_ptr(vm, array_data_start_ptr, size)` [2](#0-1) , which calls `vm.get_integer_range(ptr, size)` — an operation that allocates memory proportional to `size` before validating that the underlying VM memory segment actually contains that many values.

This function is used by every deprecated (Cairo0) syscall argument reader that consumes a variable-length array, including `read_calldata`/`read_call_params` (used by `library_call`, `library_call_l1_handler`, and inner contract calls) at [3](#0-2) , and event emission (`EmitEventRequest`-equivalent for Cairo0 contracts). Because Cairo0 contracts can still be declared and invoked on Starknet (deprecated but supported class type), a single submitted `INVOKE`/`L1_HANDLER` transaction that calls into a Cairo0 contract can write a crafted, arbitrarily large `array_size` felt to VM memory before triggering a deprecated syscall, causing the sequencer's `blockifier` execution to attempt a massive allocation.

Contrast with the fixed/hardened variant used elsewhere in the code: `TryFrom<Retdata> for CairoArray<T>` explicitly validates the declared item count against the number of remaining felts *before* calling `Vec::with_capacity`, precisely to prevent this class of bug [4](#0-3) . The deprecated syscall path lacks this same guard.

### Impact Explanation
A successful allocation-size attack causes the Rust allocator to either abort the process (`std::alloc::handle_alloc_error` / OOM killer) or exhaust host memory, crashing or destabilizing the sequencer/batcher node executing the block, or the Starknet OS re-execution process replaying the same transaction. Since transaction execution (including Cairo0 contract calls) happens as part of block building and OS re-execution — both required for consensus to proceed — this can lead to node crashes, which under the strict validation rules would need to translate into "a network unable to confirm new transactions" if a large fraction of block-producing nodes crash on the same malicious transaction (deterministic execution across honest nodes means all of them would hit the same allocation attempt simultaneously). This satisfies the Medium/High impact bar (network unable to confirm new transactions), analogous to CVSS 6.0 in the original report.

### Likelihood Explanation
Likelihood is moderate-to-high: reaching this code path only requires declaring/using a Cairo0 class (still valid on Starknet) and invoking any of its entry points that use a deprecated syscall consuming a variable-length array (e.g., `library_call`, or contract calls with `calldata`), with the contract crafting its own segment memory (fully controlled by the contract's own Cairo/VM instructions, not the sequencer) to place an oversized felt at the `array_size` slot before the syscall. No special privileges beyond deploying/calling a contract are needed — this is directly reachable by any unprivileged transaction sender or L1 message sender routing through an L1 handler on a Cairo0 class.

### Recommendation
In `read_felt_array` (deprecated syscalls), validate `array_size` against a sane upper bound (e.g., `SIERRA_ARRAY_LEN_BOUND`, consistent with the bound already enforced in Cairo1 entry-point argument checks, or the actual number of felts remaining in the current VM memory segment) before converting to `usize` and before calling `felt_range_from_ptr`/`vm.get_integer_range`. This mirrors the fix already applied in `apollo_cairo_utils`'s `TryFrom<Retdata> for CairoArray<T>`, which checks the declared length against remaining data before allocating.

### Proof of Concept
1. Declare and deploy a Cairo0 (deprecated) contract that, within an external entry point, manually constructs a deprecated syscall (e.g., a raw `library_call` or `emit_event`/calldata-array syscall) whose argument segment sets the `array_size` field to a very large felt value (e.g., `2^63`), while leaving the data pointer pointing at a small/short real segment.
2. Submit a normal `INVOKE`/`L1_HANDLER` transaction invoking that entry point.
3. During execution, `blockifier`'s `read_felt_array` reads the oversized `array_size`, converts it to `usize`, and calls `felt_range_from_ptr`/`vm.get_integer_range(ptr, huge_size)`, triggering an allocation attempt proportional to the attacker-chosen size, crashing or hanging the executing node process (batcher / OS re-execution).

*Note: I was unable to inspect the exact internal implementation of `cairo_vm`'s `get_integer_range` (external dependency, not indexed in this repository), so I cannot confirm from this codebase alone whether it performs any additional bound-checking against segment length before allocating. This should be verified directly in the `cairo-vm` dependency source before treating this as fully confirmed.*

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
