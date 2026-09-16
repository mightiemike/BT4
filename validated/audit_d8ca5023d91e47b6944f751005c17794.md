## Analysis: Unbounded array-size read in deprecated (Cairo 0) syscall array parsing

I found a plausible analog to CVE-2016-9427's "huge/attacker-controlled allocation size" bug class in the Cairo0 (`deprecated_syscalls`) syscall-argument parsing path of `blockifier`, reachable from any declared/invoked Cairo0 class.

### Title
Unbounded, unchecked array-size felt drives a huge memory-range read/allocation in Cairo0 syscall argument parsing — ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
`read_felt_array` in the deprecated (Cairo0) syscall executor reads an `array_size` felt directly from VM memory and converts it to a `usize` with no upper-bound sanity check before passing it to `felt_range_from_ptr`, which forwards it as the `size` argument to `vm.get_integer_range` (`cairo-vm`). [1](#0-0) [2](#0-1) 

### Finding Description
`array_size` is not derived from the actual size of a backing memory segment — it is a raw felt value written by the executing contract's own Cairo0 bytecode into the syscall request layout (e.g. for `EmitEventRequest`, `CallContractRequest`, `DeployRequest`, `library_call`). A contract author fully controls this value. [3](#0-2) [4](#0-3) 

Unlike the newer (Cairo1) syscall path, which computes `array_size` as the difference between two pointers inside the same allocated segment (`array_data_end_ptr - array_data_start_ptr`), and is therefore bounded by real memory, the deprecated path takes the size felt at face value: [5](#0-4) 

```rust
let array_size = felt_from_ptr(vm, ptr)?;
...
Ok(felt_range_from_ptr(vm, array_data_start_ptr, usize::try_from(array_size.to_biguint())?)?)
```
There is no cap comparable to the gateway's `max_calldata_length`/`max_signature_length` checks (which only bound the *outer* transaction's calldata/signature, not values a contract can pass internally to a syscall). [6](#0-5) 

This mirrors the bdwgc bug class: a size value taken directly from untrusted input is passed, unchecked, into a memory-range read/allocation routine, which can crash the process (OOM/heap error) — CVE-2016-9427's "huge allocation" pattern, just triggered here via `usize::try_from` + `get_integer_range` rather than a raw `malloc`.

### Impact Explanation
Since every full node (validator/sequencer) that re-executes the transaction independently runs this same code path, a crafted Cairo0 class that issues, e.g., `emit_event` or `library_call` with an astronomically large `keys_len`/`data_len`/`calldata_size` felt (but a normal, small pointer) can cause `felt_range_from_ptr`/`get_integer_range` to attempt to read/allocate a huge range. This can crash or OOM-abort the executing node process during block building/re-execution — a network-wide denial of service if enough nodes hit the same transaction, since it is deterministic (same tx, same crash on every honest node).

### Likelihood Explanation
Reachability requires only:
1. Declaring a Cairo0 class with a `@raw_input` entry point that forwards an attacker-chosen size into a legacy syscall (feature contracts already demonstrate this pattern, e.g. `delegate_proxy.cairo`'s `emit_event_raw`). [7](#0-6) 
2. Deploying and invoking it — an ordinary unprivileged account action.

I was **unable to fully verify** the exact internal behavior of `cairo-vm`'s `get_integer_range`/`get_continuous_range` (it lives in the external `cairo-vm` dependency, not in this repository, so I could not confirm whether it pre-validates `size` against segment bounds before allocating). This is the main source of uncertainty in this finding — if `cairo-vm` already caps or lazily validates the range before allocating, the practical impact would be reduced to an ordinary bounded error rather than a crash.

### Recommendation
Add an explicit upper bound (e.g., matching `max_calldata_length`/a syscall-specific constant) on `array_size` in `read_felt_array` (deprecated path) immediately after reading it from `felt_from_ptr`, before calling `felt_range_from_ptr`, mirroring the bound already enforced for top-level transaction calldata at the gateway layer.

### Proof of Concept
1. Declare and deploy a Cairo0 contract with a `@raw_input` external function that calls `emit_event` (or `library_call`/`call_contract`) with `data_len` set to a large felt (e.g. `2**40`) while `data` points to a small/near-empty segment.
2. Invoke this function via a normal `INVOKE` transaction.
3. During execution, `read_felt_array` reads the huge `array_size`, and `felt_range_from_ptr` → `vm.get_integer_range` is invoked with that size, potentially triggering a large allocation attempt/crash on every executing node.

**Confidence caveat:** Because the crash-inducing behavior ultimately depends on the external `cairo-vm` crate's `get_integer_range` implementation (not present in this indexed codebase), this finding should be validated by actually running the PoC against the target `cairo-vm` version before treating it as fully confirmed.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L860-876)
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

**File:** crates/blockifier/src/execution/deprecated_syscalls/mod.rs (L270-292)
```rust
// EmitEvent syscall.

#[derive(Debug, Eq, PartialEq)]
pub struct EmitEventRequest {
    pub content: EventContent,
}

impl SyscallRequest for EmitEventRequest {
    // The Cairo struct contains: `keys_len`, `keys`, `data_len`, `data`·
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> DeprecatedSyscallExecutorBaseResult<EmitEventRequest> {
        let keys = read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?
            .into_iter()
            .map(EventKey)
            .collect();
        let data = EventData(read_felt_array::<DeprecatedSyscallExecutorBaseError>(vm, ptr)?);

        Ok(EmitEventRequest { content: EventContent { keys, data } })
    }
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L152-178)
```rust
    /// Validates the transaction calldata size.
    /// This includes client-side proof facts when present.
    fn validate_tx_extended_calldata_size(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let total_length = match tx {
            RpcTransaction::Declare(_) => return Ok(()),

            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                tx.constructor_calldata.0.len()
            }

            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                tx.calldata.0.len() + tx.proof_facts.0.len()
            }
        };

        if total_length > self.config.max_calldata_length {
            return Err(StatelessTransactionValidatorError::CalldataTooLong {
                calldata_length: total_length,
                max_calldata_length: self.config.max_calldata_length,
            });
        }

        Ok(())
    }
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo0/delegate_proxy.cairo (L22-29)
```text
@external
@raw_input
func emit_event_raw{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    selector: felt, calldata_size: felt, calldata: felt*
) {
    emit_event(keys_len=calldata_size, keys=calldata, data_len=calldata_size, data=calldata);
    return ();
}
```
