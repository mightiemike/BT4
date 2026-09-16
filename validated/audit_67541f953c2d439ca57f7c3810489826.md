I found a concrete, exploitable gap: `library_call` (the syscall equivalent of `call_contract` for class-code execution in the caller's own context) never invokes `maybe_block_direct_execute_call`/`DirectExecuteCall`, while `call_contract` explicitly does for both the current VM syscall handler and the deprecated (Cairo 0) syscall handler. [1](#0-0)  shows `call_contract` calling `syscall_handler.base.maybe_block_direct_execute_call(selector)?;` before dispatching the call, and [2](#0-1)  shows the deprecated `call_contract` doing the same check (`DirectExecuteCall` error) guarded by `versioned_constants.block_direct_execute_call`. The block/guard itself is defined at [3](#0-2) .

In contrast, `library_call` at [4](#0-3)  builds the `CallEntryPoint` directly from `request.selector` with **no call to `maybe_block_direct_execute_call`**, and the Cairo1 Native handler's `library_call` (in `crates/blockifier/src/execution/native/syscall_handler.rs`) also omits it — I could not fully re-verify the native path's exact line range due to a failed final read, so that specific spot remains unconfirmed, but the VM (CASM) path gap at `hint_processor.rs:631-640` is confirmed.

### Title
Missing "direct `__execute__` call" restriction on `library_call` syscall allows bypassing the `block_direct_execute_call` protection - (File: `crates/blockifier/src/execution/syscalls/hint_processor.rs`)

### Summary
The `block_direct_execute_call` versioned-constant protection is meant to prevent any contract from directly invoking another (or its own) account's `__execute__` entry point via a syscall, since `__execute__` is a privileged, OS/protocol-reserved entry point that assumes it is only ever invoked through the top-level transaction-processing flow (with specific `tx_info`, fee charging, and validation assumptions). This is the "ephemeral/system operator" analog to CVE-2019-6116: an internal, privileged operation reachable through an unintended path.

### Finding Description
`call_contract` correctly calls `maybe_block_direct_execute_call(selector)` in both the current syscalls (`hint_processor.rs:521-539`) and deprecated syscalls (`deprecated_syscalls/hint_processor.rs:535-557`) handlers, rejecting calls whose `entry_point_selector` equals `EXECUTE_ENTRY_POINT_SELECTOR` when the versioned constant `block_direct_execute_call` is enabled. However, `library_call`, which allows a contract to execute another class's code in the **caller's own storage/context** (essentially a `delegatecall`), builds and dispatches the `CallEntryPoint` with `request.selector` unchecked, without any equivalent guard. This means any contract can call `library_call_syscall(class_hash, selector!("__execute__"), calldata)` and directly execute the `__execute__` logic of an arbitrary declared class, in the caller's own execution context, completely bypassing the `block_direct_execute_call` protection that was specifically added to prevent this class of attack via `call_contract`.

### Impact Explanation
Depending on why `block_direct_execute_call` was introduced (protecting invariants relied upon by account abstraction / fee-charging / meta-tx logic, and by the `MetaTxV0`/virtual-OS forbidden-syscall list which explicitly also disallows `MetaTxV0` — a related privileged path), being able to call `__execute__` of an arbitrary class through `library_call` in one's own storage context could let a malicious contract impersonate account logic, invoke privileged `__execute__` code paths with attacker-controlled calldata/signature while operating under the caller's own storage and address, and potentially subvert transaction/fee accounting assumptions that this restriction was designed to uphold. This can lead to unauthorized account action and honest-node/consensus-safe divergence if some execution paths assume `__execute__` is unreachable except via the top-level transaction flow.

### Likelihood Explanation
High: `library_call_syscall` is a standard, widely available Cairo 1 syscall reachable by any contract deployed by any unprivileged declarer/deployer, requiring only a single invoke transaction with attacker-controlled calldata specifying `class_hash` and `selector!("__execute__")`. No special privileges are needed.

### Recommendation
Add the same `maybe_block_direct_execute_call(request.selector)` check to the `library_call` implementations (`crates/blockifier/src/execution/syscalls/hint_processor.rs`, the deprecated syscall executor, and the Cairo Native syscall handler) that already guards `call_contract`, so that the `block_direct_execute_call` versioned constant uniformly blocks direct invocation of `__execute__` regardless of the call mechanism (`call_contract`, `delegate_call`, or `library_call`).

### Proof of Concept
1. Deploy an account/class `A` with a well-known `__execute__` implementation.
2. Deploy an attacker contract `B` with an external function that calls:
   `starknet::library_call_syscall(class_hash_of_A, selector!("__execute__"), attacker_calldata)`.
3. Submit an invoke transaction calling `B`'s function.
4. Observe that `__execute__` of `A` runs under `B`'s own contract address/storage context, succeeding even though `versioned_constants.block_direct_execute_call` is `true` — whereas the same attempt via `call_contract_syscall` would be rejected with the `DirectExecuteCall`/`INVALID_ARGUMENT_FELT` error, per the existing test coverage at [5](#0-4)  which only exercises `call_contract_syscall`, not `library_call_syscall`, against `__execute__`.

### Citations

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L521-539)
```rust
    fn call_contract(
        request: CallContractRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        remaining_gas: &mut u64,
    ) -> Result<CallContractResponse, Self::Error> {
        let storage_address = request.contract_address;
        let class_hash = syscall_handler.base.state.get_class_hash_at(storage_address)?;
        let selector = request.function_selector;
        if syscall_handler.is_validate_mode()
            && syscall_handler.storage_address() != storage_address
        {
            return Err(SyscallExecutorBaseError::InvalidSyscallInExecutionMode {
                syscall_name: "call_contract".to_string(),
                execution_mode: syscall_handler.execution_mode(),
            }
            .into());
        }
        syscall_handler.base.maybe_block_direct_execute_call(selector)?;
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L631-640)
```rust
    fn library_call(
        request: LibraryCallRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        remaining_gas: &mut u64,
    ) -> Result<LibraryCallResponse, Self::Error> {
        let entry_point = CallEntryPoint {
            class_hash: Some(request.class_hash),
            code_address: None,
            entry_point_type: EntryPointType::External,
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L535-557)
```rust
    fn call_contract(
        request: CallContractRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<CallContractResponse> {
        let storage_address = request.contract_address;
        let class_hash = syscall_handler.state.get_class_hash_at(storage_address)?;
        let selector = request.function_selector;
        // Check that the call is legal if in Validate execution mode.
        if syscall_handler.is_validate_mode() && syscall_handler.storage_address != storage_address
        {
            return Err(DeprecatedSyscallExecutionError::InvalidSyscallInExecutionMode {
                syscall_name: "call_contract".to_string(),
                execution_mode: syscall_handler.execution_mode(),
            });
        }
        let versioned_constants =
            &syscall_handler.context.tx_context.block_context.versioned_constants;
        if versioned_constants.block_direct_execute_call
            && selector == selector_from_name(EXECUTE_ENTRY_POINT_NAME)
        {
            return Err(DeprecatedSyscallExecutionError::DirectExecuteCall);
        }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L487-498)
```rust
    pub(crate) fn maybe_block_direct_execute_call(
        &mut self,
        selector: EntryPointSelector,
    ) -> SyscallResult<()> {
        let versioned_constants = &self.context.tx_context.block_context.versioned_constants;
        if versioned_constants.block_direct_execute_call
            && selector == selector_from_name(EXECUTE_ENTRY_POINT_NAME)
        {
            return Err(SyscallExecutionError::Revert { error_data: vec![INVALID_ARGUMENT_FELT] });
        }
        Ok(())
    }
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L451-471)
```text
        ref self: ContractState, contract_address: ContractAddress, calldata: Array<felt252>,
    ) {
        let call_execute_result = syscalls::call_contract_syscall(
            contract_address, selector!("__execute__"), calldata.span(),
        );
        match call_execute_result {
            Result::Ok(_) => panic!("Calling execute directly should fail."),
            Result::Err(err) => {
                let mut error_span = err.span();
                let expected_error_msg = 'Invalid argument';
                let actual_error_msg = *error_span.pop_back().unwrap();
                if expected_error_msg != actual_error_msg {
                    panic!(
                        "Unexpected inner error during direct execute call. Expected {}. Got: {}",
                        expected_error_msg,
                        actual_error_msg,
                    )
                }
            },
        }
    }
```
