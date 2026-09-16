### Title
Unbounded memory allocation via attacker-controlled array-size felt in deprecated (Cairo0) syscall calldata parsing — ([File: crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs])

### Summary
The Spring Data Commons advisory describes a DoS caused by allocating memory sized directly from an attacker-supplied length value before validating it against the actual available data. The sequencer's deprecated (Cairo0) syscall calldata reader has the same pattern: it reads a raw `array_size` felt written by executing contract code, converts it to `usize`, and passes it straight into a VM memory-range read that allocates a buffer of that size — with no upper bound check and no validation that the segment actually contains that many elements before allocating.

### Finding Description
`read_felt_array` in the deprecated syscall path reads an unvalidated size value from VM memory and uses it to size an allocation: [1](#0-0) 

```
let array_size = felt_from_ptr(vm, ptr)?;
let array_data_start_ptr = vm.get_relocatable(*ptr)?;
...
Ok(felt_range_from_ptr(vm, array_data_start_ptr, usize::try_from(array_size.to_biguint())?)?)
```

`felt_range_from_ptr` forwards this attacker/contract-controlled `size` directly into `vm.get_integer_range(ptr, size)`: [2](#0-1) 

This value is used, via `read_calldata`/`read_call_params`, to build the `Calldata` for `CallContract`, `Deploy`, and `LibraryCall` deprecated syscalls: [3](#0-2) [4](#0-3) 

Critically, `array_size` is not bounded by anything before the range read is attempted — unlike the newer (Cairo1) syscall implementation, which derives the array length as the *difference between two pointers already present in the VM's own segments* rather than trusting an arbitrary felt: [5](#0-4) 

Because Cairo0 declaration/redeclaration is still a reachable code path (gated only by the `disable_cairo0_redeclaration` versioned constant, not eliminated), and Cairo0 contract bytecode can compute the array-size operand for `call_contract`/`deploy`/`library_call` at runtime from data influenced by the caller (e.g., a proxy-style Cairo0 contract that forwards a length derived from its own inbound calldata), an attacker can arrange for this size felt to be an enormous value (e.g., close to `usize::MAX`). This causes `vm.get_integer_range` to attempt a huge allocation before it can detect that the underlying segment doesn't actually contain that many cells, aborting the sequencer process with an out-of-memory condition — the same allocate-before-validate defect described in the Spring Data Commons advisory. [6](#0-5) 

### Impact Explanation
A successful trigger crashes the sequencer/RPC node process executing the transaction (out-of-memory abort), which is a Denial of Service: it can stall block production or, if reproduced independently by honest and non-honest nodes with different memory limits, cause honest-node divergence in ability to process the block. This maps to CWE-400 (uncontrolled resource consumption), matching the referenced advisory's bug class.

### Likelihood Explanation
Reachability requires only: (1) a Cairo0 contract already deployed/declared on-chain (still permitted unless `disable_cairo0_redeclaration` is set) whose logic forwards an attacker-influenced value as the calldata-array-size operand of a deprecated syscall, and (2) any account issuing a normal `Invoke` transaction that triggers that code path. No special privileges, staker/prover role, or malicious operator behavior is required — a single crafted transaction from an ordinary sender is sufficient once such a contract exists or is deployed by the attacker.

### Recommendation
- Validate `array_size` in `read_felt_array` (deprecated_syscalls/hint_processor.rs) against a sane upper bound (e.g., the maximum syscall calldata size already enforced elsewhere in the gateway/blockifier) before calling `felt_range_from_ptr`/`vm.get_integer_range`.
- Alternatively, align the deprecated syscall implementation with the Cairo1 syscall approach: derive array length from validated pointer arithmetic within already-allocated VM segments rather than trusting an arbitrary felt value.
- Add a regression test asserting that a declared/executed Cairo0 contract cannot force allocation of arbitrarily large buffers via the deprecated `CallContract`/`Deploy`/`LibraryCall` syscalls.

### Proof of Concept
1. Declare (or use an already-declared) Cairo0 contract whose code, upon invocation, computes a very large felt (e.g., near `Felt::MAX` reduced to fit `usize`) and stores it at the memory location used as `array_size` before invoking the `call_contract`, `deploy`, or `library_call` deprecated syscall.
2. Send an ordinary `Invoke` transaction to that entry point.
3. During execution, `read_felt_array` (crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:940-953) reads the crafted felt as `array_size`, converts it to `usize`, and calls `felt_range_from_ptr` → `vm.get_integer_range(ptr, array_size)` (crates/blockifier/src/execution/execution_utils.rs:228-237), attempting to allocate a buffer of that size before the VM can determine the segment doesn't actually hold that many values, aborting the process.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L869-884)
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

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L940-953)
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

**File:** crates/blockifier/src/execution/deprecated_syscalls/mod.rs (L205-251)
```rust
impl SyscallRequest for CallContractRequest {
    fn read(
        vm: &VirtualMachine,
        ptr: &mut Relocatable,
    ) -> DeprecatedSyscallExecutorBaseResult<CallContractRequest> {
        let contract_address = ContractAddress::try_from(felt_from_ptr(vm, ptr)?)?;
        let (function_selector, calldata) = read_call_params(vm, ptr)?;

        Ok(CallContractRequest { contract_address, function_selector, calldata })
    }
}

pub type CallContractResponse = SingleSegmentResponse;

// DelegateCall and DelegateCallL1Handler syscalls.

pub type DelegateCallRequest = CallContractRequest;
pub type DelegateCallResponse = CallContractResponse;

// Deploy syscall.

#[derive(Debug, Eq, PartialEq)]
pub struct DeployRequest {
    pub class_hash: ClassHash,
    pub contract_address_salt: ContractAddressSalt,
    pub constructor_calldata: Calldata,
    pub deploy_from_zero: bool,
}

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L860-871)
```rust
pub fn read_felt_array<TErr>(vm: &VirtualMachine, ptr: &mut Relocatable) -> Result<Vec<Felt>, TErr>
where
    TErr: From<StarknetApiError> + From<VirtualMachineError> + From<MemoryError> + From<MathError>,
{
    let array_data_start_ptr = vm.get_relocatable(*ptr)?;
    *ptr = (*ptr + 1)?;
    let array_data_end_ptr = vm.get_relocatable(*ptr)?;
    *ptr = (*ptr + 1)?;
    let array_size = (array_data_end_ptr - array_data_start_ptr)?;

    Ok(felt_range_from_ptr(vm, array_data_start_ptr, array_size)?)
}
```

**File:** crates/blockifier/src/transaction/transactions.rs (L163-175)
```rust
        match &self.tx {
            starknet_api::transaction::DeclareTransaction::V0(_)
            | starknet_api::transaction::DeclareTransaction::V1(_) => {
                if context.tx_context.block_context.versioned_constants.disable_cairo0_redeclaration
                {
                    try_declare(self, state, class_hash, None)?
                } else {
                    // We allow redeclaration of the class for backward compatibility.
                    // In the past, we allowed redeclaration of Cairo 0 contracts since there was
                    // no class commitment (so no need to check if the class is already declared).
                    state.set_contract_class(class_hash, self.contract_class().try_into()?)?;
                }
            }
```
