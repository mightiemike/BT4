Based on my research, I found a concrete analog reachable from ordinary contract execution: an unbounded, attacker-controlled allocation from a Cairo-memory-supplied length field, performed **before** gas is charged for it.

### Title
Unbounded memory allocation from contract-controlled array length before gas accounting in syscall request parsing - (File: `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`)

### Summary
`read_felt_array`, used to parse variable-length syscall inputs (`emit_event` keys/data, `send_message_to_l1` payload, `meta_tx_v0` signature), reads a length felt directly from contract-controlled Cairo VM memory and converts it to a `usize` with no upper-bound validation, then materializes a `Vec<Felt>` of that size, before the syscall dispatcher performs any gas check for the operation.

### Finding Description
`read_felt_array` reads `array_size` straight from the executing contract's own memory and converts it with `usize::try_from(array_size.to_biguint())?`, with no cap against a sane maximum, then calls `felt_range_from_ptr` to materialize that many felts: [1](#0-0) 

This helper is used by multiple syscall request parsers that are reachable from ordinary (non-privileged) contract execution — `EmitEventRequest`, `SendMessageToL1Request`, and `MetaTxV0Request`: [2](#0-1) [3](#0-2) [4](#0-3) 

Crucially, the generic `execute_syscall` dispatcher reads the *entire request* (including these variable-length array reads/allocations) via `SyscallRequestWrapper::<Request>::read(...)` **before** it computes the syscall's linear-factor gas cost and checks it against the caller's remaining gas: [5](#0-4) 

The gas cost used for the check (`syscall_gas_cost.get_syscall_cost(u64_from_usize(request.get_linear_factor_length()))`) is derived from the already-parsed `request`, meaning the potentially oversized allocation has already occurred by the time gas is checked. This mirrors the CVE-2017-12467 bug class: a length/size field taken from untrusted input drives memory allocation without a bound check or prior resource accounting, enabling a memory-consumption denial of service.

### Impact Explanation
A contract executed as part of a normal transaction (deployed by any unprivileged account) can invoke `emit_event`, `send_message_to_l1`, or `meta_tx_v0` with a crafted length felt (e.g., near the maximum representable `usize`) written into its own memory before the syscall. This triggers an attempt to allocate/read a felt array of that size in `read_felt_array`/`felt_range_from_ptr` prior to any gas-sufficiency check for the operation's actual size, risking large memory allocation or process abort (OOM) on the sequencer executing the transaction — a network-wide denial-of-service condition affecting the ability to process transactions, since any sequencer executing this transaction is impacted the same way (honest-node crash, not merely a single operator's fault).

### Likelihood Explanation
This is reachable with a single ordinary Cairo0-style syscall invocation from within contract code executed during normal transaction execution (`blockifier` execution path), requiring no special privileges — only writing an oversized felt to the contract's own execution memory prior to issuing `emit_event`/`send_message_to_l1`/`meta_tx_v0`. No malicious operator, proposer, or peer collusion is needed.

### Recommendation
Validate the declared array length against a sane upper bound (e.g., the maximum possible remaining VM memory segment size or a configured protocol limit) immediately after reading it in `read_felt_array`, before calling `felt_range_from_ptr`/allocating any buffer — mirroring the fix already applied in `apollo_cairo_utils`'s `CairoArray::try_from`, which validates the declared count against `iter.len()` before calling `Vec::with_capacity`: [6](#0-5) 
Additionally, consider charging/verifying gas for the declared linear-factor length before parsing variable-length syscall request fields, rather than after.

### Proof of Concept
1. Deploy or use any contract capable of triggering `emit_event`, `send_message_to_l1`, or the `meta_tx_v0` syscall (all reachable via ordinary external/`__execute__` calls).
2. In the Cairo code (or by crafting raw memory contents consumed by the VM), set the `keys_len`/`data_len` (for `emit_event`), `payload_size` (for `send_message_to_l1`), or signature length (for `meta_tx_v0`) felt to a very large value that still fits within `usize` (e.g., close to `usize::MAX` on 64-bit hosts).
3. Submit this as a normal transaction. During execution, `read_felt_array` (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:931-951`) converts the felt to `usize` and calls `felt_range_from_ptr` to materialize the array before `execute_syscall` (`crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs:674-703`) has verified the caller has enough gas for an array of that size.
4. Observe attempted large allocation/read, which can exhaust sequencer memory or abort the process, denying service to other transactions in the block/mempool.

I could not fully verify the internal implementation of `felt_range_from_ptr` / the underlying `cairo-vm` memory-range read (it may partially validate against actual segment bounds before completing the full allocation), since that logic lives outside the directly indexed code I could inspect in this session — a Devin session with full repository/dependency access would be needed to confirm the exact allocation behavior of `felt_range_from_ptr` and `cairo-vm`'s range-read primitives before concluding the precise OOM trigger threshold.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L245-256)
```rust
impl SyscallRequest for EmitEventRequest {
    // The Cairo struct contains: `keys_len`, `keys`, `data_len`, `data`·
    fn read(vm: &VirtualMachine, ptr: &mut Relocatable) -> SyscallBaseResult<EmitEventRequest> {
        let keys = read_felt_array::<SyscallExecutorBaseError>(vm, ptr)?
            .into_iter()
            .map(EventKey)
            .collect();
        let data = EventData(read_felt_array::<SyscallExecutorBaseError>(vm, ptr)?);

        Ok(EmitEventRequest { content: EventContent { keys, data } })
    }
}
```

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L364-377)
```rust
impl SyscallRequest for MetaTxV0Request {
    fn read(vm: &VirtualMachine, ptr: &mut Relocatable) -> SyscallBaseResult<MetaTxV0Request> {
        let contract_address = ContractAddress::try_from(felt_from_ptr(vm, ptr)?)?;
        let (entry_point_selector, calldata) = read_call_params(vm, ptr)?;
        let signature =
            TransactionSignature(read_felt_array::<SyscallExecutorBaseError>(vm, ptr)?.into());

        Ok(MetaTxV0Request { contract_address, entry_point_selector, calldata, signature })
    }

    fn get_linear_factor_length(&self) -> usize {
        self.calldata.0.len()
    }
}
```

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L405-417)
```rust
impl SyscallRequest for SendMessageToL1Request {
    // The Cairo struct contains: `to_address`, `payload_size`, `payload`.
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> SyscallBaseResult<SendMessageToL1Request> {
        let to_address_felt = felt_from_ptr(vm, ptr)?;
        let to_address = to_address_felt.into();
        let payload = L2ToL1Payload(read_felt_array::<SyscallExecutorBaseError>(vm, ptr)?);

        Ok(SendMessageToL1Request { message: MessageToL1 { to_address, payload } })
    }
}
```

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L674-703)
```rust
    let syscall_gas_cost = syscall_executor
        .get_gas_cost_from_selector(&selector)
        .map_err(|error| SyscallExecutorBaseError::GasCost { error, selector })?;

    let SyscallRequestWrapper { gas_counter, request } =
        SyscallRequestWrapper::<Request>::read(vm, syscall_executor.get_mut_syscall_ptr())?;

    let syscall_gas_cost =
        syscall_gas_cost.get_syscall_cost(u64_from_usize(request.get_linear_factor_length()));
    let syscall_base_cost = syscall_executor.get_syscall_base_gas_cost();

    // Sanity check for preventing underflow.
    assert!(
        syscall_gas_cost >= syscall_base_cost,
        "Syscall gas cost must be greater than base syscall gas cost"
    );

    // Refund `SYSCALL_BASE_GAS_COST` as it was pre-charged.
    let required_gas = syscall_gas_cost - syscall_base_cost;

    if gas_counter < required_gas {
        //  Out of gas failure.
        let response: SyscallResponseWrapper<Response> = SyscallResponseWrapper::Failure {
            gas_counter,
            revert_data: RevertData::new_normal(vec![OUT_OF_GAS_ERROR_FELT]),
        };
        response.write(vm, syscall_executor.get_mut_syscall_ptr())?;

        return Ok(());
    }
```

**File:** crates/apollo_cairo_utils/src/lib.rs (L118-129)
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
