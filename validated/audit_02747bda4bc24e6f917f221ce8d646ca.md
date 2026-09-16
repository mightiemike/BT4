Based on my research, I found a directly analogous pattern in the blockifier's syscall array-reading code, but I was unable to fully verify the allocation bound-checking due to running out of tool iterations. I'll present what was confirmed and flag the remaining uncertainty explicitly.

### Title
Unbounded memory allocation from user-controlled array-length felt in syscall array reading - (File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs)

### Summary
The jsPDF advisory describes a DoS caused by trusting an attacker-controlled "size" field (GIF width/height) to drive a large memory allocation before any sanity bound is applied. The analogous pattern in the sequencer is `read_felt_array` in [1](#0-0) , which reads an `array_size` felt directly from Cairo VM memory (a value fully controlled by the executing contract's bytecode/calldata) and converts it to a `usize` that is then passed to `felt_range_from_ptr` to read/allocate that many felts, with no explicit upper bound check visible at this call site.

### Finding Description
`read_felt_array` reads `array_size` via `felt_from_ptr` and, unless it is exactly zero, calls `felt_range_from_ptr(vm, array_data_start_ptr, usize::try_from(array_size.to_biguint())?)` [2](#0-1) . The only check performed is the `TryFrom<BigUint>` conversion to `usize`, which merely prevents values that don't fit a machine word — it does not bound the value to anything close to a reasonable calldata/array size. This function is used by the deprecated syscalls hint processor and referenced again by the syscalls `hint_processor.rs` and `vm_syscall_utils.rs`, i.e., it backs syscalls that a deployed contract can invoke directly (such as reading arrays/payloads passed to `send_message_to_l1` and similar syscalls) [3](#0-2) .

Because `array_size` originates from data written into the VM's memory by the executing (attacker-supplied) Cairo program, a contract deployer/caller can set this to a very large value (up to `usize::MAX` on 64-bit hosts, since felts trivially fit that range) before any resource/gas accounting for the resulting allocation is applied, mirroring the jsPDF pattern of trusting a size field to drive allocation.

### Impact Explanation
If `felt_range_from_ptr` (or the downstream `Vec` allocation it performs) does not itself enforce a strict bound tied to already-metered gas/resources before allocating, a malicious contract could force the sequencer node to attempt an enormous allocation during transaction execution, resulting in an out-of-memory condition or excessive CPU during zeroing/allocation. This would crash or hang the sequencer process executing the transaction, potentially affecting block production availability — a network-unable-to-confirm-new-transactions scenario if triggered broadly. I was not able to confirm within the available iterations whether `felt_range_from_ptr` performs its own bound check (e.g., against remaining gas or a hard-coded max) before allocating, which is the crux of whether this is truly exploitable or already mitigated.

### Likelihood Explanation
Likelihood is uncertain without confirming the internals of `felt_range_from_ptr` in `crates/blockifier/src/execution/execution_utils.rs`. If gas/resource metering already gates array reads proportionally (as is typical for well-audited VM syscall implementations), this would not be exploitable, since the calling contract would run out of gas before the array could be read/allocated. This caveat must be resolved by direct code inspection.

### Recommendation
Verify `felt_range_from_ptr`'s implementation in `crates/blockifier/src/execution/execution_utils.rs` to confirm whether allocation size is bounded by already-charged gas/resources before the `Vec` is allocated. If not, add an explicit maximum bound (e.g., tied to segment/memory limits or remaining gas) prior to calling `Vec::with_capacity`/allocation in `felt_range_from_ptr`, mirroring the fix pattern of validating dimensions before allocation in the jsPDF patch.

### Proof of Concept
Not constructed — this analog requires confirming the implementation of `felt_range_from_ptr` (not retrieved within tool budget) to determine whether a concrete unmetered allocation path exists. This is a partial, unverified finding; further investigation via a Devin session with full file access is recommended to confirm root cause before treating this as a confirmed vulnerability.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L809-826)
```rust
    fn send_message_to_l1(
        request: SendMessageToL1Request,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<SendMessageToL1Response> {
        let execution_context = &mut syscall_handler.context;
        if !execution_context.tx_context.block_context.chain_info.is_l3 {
            EthAddress::try_from(request.message.to_address)?;
        }
        let ordered_message_to_l1 = OrderedL2ToL1Message {
            order: execution_context.n_sent_messages_to_l1,
            message: request.message,
        };
        syscall_handler.l2_to_l1_messages.push(ordered_message_to_l1);
        execution_context.n_sent_messages_to_l1 += 1;

        Ok(SendMessageToL1Response {})
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
