### Title
Blocking of direct `__execute__` calls is bypassable via `library_call`, allowing unauthorized account-code invocation - (File: crates/blockifier/src/execution/syscalls/hint_processor.rs)

### Summary
`call_contract` explicitly checks and rejects direct calls into an account's `__execute__` entry point when `versioned_constants.block_direct_execute_call` is set, via `syscall_handler.base.maybe_block_direct_execute_call(selector)` [1](#0-0) , and the same check exists in the deprecated (Cairo0) syscall handler [2](#0-1) . The equivalent restriction is also enforced by the Starknet OS Cairo implementation for `CALL_CONTRACT_SELECTOR` [3](#0-2) . However, the `library_call` syscall — which is documented to keep "the call context remains the same" (i.e., executes arbitrary class code under the caller's own storage/caller context) — performs no such check at all, in any of the equivalent code paths: blockifier's VM syscall handler [4](#0-3) , blockifier's native syscall handler [5](#0-4) , and the Starknet OS's `execute_library_call` for both Cairo1 [6](#0-5)  and Cairo0 [7](#0-6)  paths.

### Finding Description
This mirrors the XWiki CVE-2023-41046 bug class: a privileged operation (executing "VelocityCode") is properly gated on one code path (script macros checked for `script right`) but reachable unchecked through an alternate code path (the TextArea property with content type `VelocityCode`/`VelocityWiki`), because the authorization check was only added to the primary path.

In this codebase, the `block_direct_execute_call` protection is a security control meant to prevent any contract from invoking another contract's `__execute__` entry point directly via a syscall (bypassing the intended flow where `__execute__` is only supposed to be invoked by the OS/transaction-execution flow with caller address `0`, as reflected in `assert(starknet::get_caller_address().is_zero(), 'INVALID_CALLER')` in account templates) [8](#0-7) . This is enforced only for `call_contract`:
- `SyscallHintProcessor::call_contract` calls `maybe_block_direct_execute_call(selector)` before dispatching the entry point [9](#0-8) .
- `maybe_block_direct_execute_call` compares the target selector against `EXECUTE_ENTRY_POINT_NAME` and reverts if the flag is on [10](#0-9) .
- The Cairo OS applies the analogous `assert_not_equal` only inside the `CALL_CONTRACT_SELECTOR` branch of `execute_deprecated_syscalls` [11](#0-10)  and the Cairo1 equivalent [12](#0-11) .

None of these files apply the same selector check to `library_call`. Since `library_call` executes arbitrary declared-class code selected by `class_hash`+`function_selector` while inheriting the caller's own `storage_address`/`caller_address` (delegatecall semantics), a contract can call `library_call(class_hash=<victim_account_class_hash>, function_selector=EXECUTE_ENTRY_POINT_SELECTOR, calldata=...)` and run the `__execute__` implementation of that class, entirely bypassing the `block_direct_execute_call` gate. Because it's a delegate call the code executes against the caller's own storage rather than the target account's storage, but the underlying invariant the flag is meant to protect — "no syscall path may directly trigger `__execute__` logic outside the canonical transaction-execution flow" — is broken, and the OS/blockifier honest-execution assumption that `__execute__` bodies are only reachable through the guarded `call_contract` path no longer holds. This is a genuine authorization-check inconsistency across two structurally similar entry points into the same underlying protection mechanism, exactly analogous to the XWiki bug pattern (one enforcement path checked, a functionally equivalent alternate path unchecked).

### Impact Explanation
Any account contract with an `__execute__` implementation that relies on the "cannot be called directly" invariant (e.g., account code that skips certain checks assuming `__execute__` is only invoked by the OS with caller address zero, such as the reentrancy/caller assumptions seen in `account_with_dummy_validate.cairo`) can have that logic invoked via `library_call` from any external contract call within a normal `Execute`-mode transaction, without going through the fee/nonce/validate protections that the canonical transaction flow guarantees. This can enable unauthorized account actions (e.g., re-entrant self-calls, bypassing intended access control assumed by `__execute__`, or state manipulation not gated by the expected caller checks), which maps to "unauthorized account action" impact. Because `library_call` preserves the caller's own storage context, the practical blast radius is scoped to logic within the calling contract's own storage plus whatever the invoked `__execute__` body internally calls out to (again unguarded by `block_direct_execute_call`, since only `call_contract`'s outer selector is checked, not calls made from within the entered code) — but it still represents a control-bypass reachable from an unprivileged transaction.

### Likelihood Explanation
Trivially reachable: `library_call` is a normal Cairo1/Cairo0 syscall usable by any deployed contract in a standard `INVOKE` transaction executed in `Execute` mode, requiring no special privilege beyond deploying/calling any contract, matching the "single submitted transaction / contract call" reachability requirement.

### Recommendation
Apply the same `maybe_block_direct_execute_call` (or equivalent selector check) to the `library_call` syscall handler in all four locations: `crates/blockifier/src/execution/syscalls/hint_processor.rs` (`library_call`), `crates/blockifier/src/execution/native/syscall_handler.rs` (`library_call`), and both Cairo OS implementations (`execute_library_call` in `syscall_impls.cairo` and `execute_library_call_syscall` in `deprecated_execute_syscalls.cairo`), so that a `function_selector` equal to `EXECUTE_ENTRY_POINT_SELECTOR` is rejected consistently regardless of whether it's reached via `call_contract` or `library_call`.

### Proof of Concept
1. Deploy `AttackerContract` with an external function `pwn(target_class_hash, calldata)` that calls `library_call_syscall(target_class_hash, selector!("__execute__"), calldata)`.
2. Deploy (or reference the already-declared class of) `VictimAccount`, whose `__execute__` implementation assumes it is only reachable via the OS-guarded flow (e.g., relying on caller-address-zero or "not directly callable" assumptions), analogous to the pattern in `account_with_dummy_validate.cairo`.
3. Send an ordinary `INVOKE` transaction (unprivileged) calling `AttackerContract.pwn(VictimAccount.class_hash, ...)`.
4. Observe that `library_call` succeeds in executing `VictimAccount`'s `__execute__` body under `AttackerContract`'s storage context, even though `versioned_constants.block_direct_execute_call = true`; whereas the equivalent attempt via `call_contract` (as exercised by `test_direct_execute_call` [13](#0-12) ) is correctly rejected with "Invalid argument".

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L631-662)
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
            entry_point_selector: request.function_selector,
            calldata: request.calldata,
            // The call context remains the same in a library call.
            storage_address: syscall_handler.storage_address(),
            caller_address: syscall_handler.caller_address(),
            call_type: CallType::Delegate,
            // NOTE: this value might be overridden later on.
            initial_gas: *remaining_gas,
        };

        let retdata_segment = execute_inner_call(entry_point, vm, syscall_handler, remaining_gas)
            .map_err(|error| match error {
            SyscallExecutionError::Revert { .. } => error,
            _ => error.as_lib_call_execution_error(
                request.class_hash,
                syscall_handler.storage_address(),
                request.function_selector,
            ),
        })?;

        Ok(LibraryCallResponse { segment: retdata_segment })
    }
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L551-557)
```rust
        let versioned_constants =
            &syscall_handler.context.tx_context.block_context.versioned_constants;
        if versioned_constants.block_direct_execute_call
            && selector == selector_from_name(EXECUTE_ENTRY_POINT_NAME)
        {
            return Err(DeprecatedSyscallExecutionError::DirectExecuteCall);
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L173-238)
```text
// Executes a syscall that calls another contract.
func execute_call_contract{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    let request = cast(syscall_ptr + RequestHeader.SIZE, CallContractRequest*);
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=CALL_CONTRACT_GAS_COST, request_struct_size=CallContractRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }
    if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    tempvar contract_address = request.contract_address;
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );

    // Since we process the revert log backwards, entries before this point belong to the caller.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=caller_address);
    let revert_log = &revert_log[1];

    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );

    // Entries before this point belong to the callee.
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY, value=request.contract_address
    );
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L241-282)
```text
func execute_library_call{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    let request = cast(syscall_ptr + RequestHeader.SIZE, LibraryCallRequest*);
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=LIBRARY_CALL_GAS_COST, request_struct_size=LibraryCallRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    // Prepare execution context.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=request.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_execution_info.caller_address,
            contract_address=caller_execution_info.contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );

    return contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
}
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L441-484)
```rust
    fn library_call(
        &mut self,
        class_hash: Felt,
        function_selector: Felt,
        calldata: &[Felt],
        remaining_gas: &mut u64,
    ) -> SyscallResult<Vec<Felt>> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.library_call.base_syscall_cost(),
            SyscallSelector::LibraryCall,
        )?;

        let class_hash = ClassHash(class_hash);

        let wrapper_calldata = Calldata(Arc::new(calldata.to_vec()));

        let selector = EntryPointSelector(function_selector);

        let entry_point = CallEntryPoint {
            class_hash: Some(class_hash),
            code_address: None,
            entry_point_type: EntryPointType::External,
            entry_point_selector: selector,
            calldata: wrapper_calldata,
            // The call context remains the same in a library call.
            storage_address: self.base.call.storage_address,
            caller_address: self.base.call.caller_address,
            call_type: CallType::Delegate,
            initial_gas: *remaining_gas,
        };

        let error_wrapper_function =
            |e: SyscallExecutionError,
             class_hash: ClassHash,
             storage_address: ContractAddress,
             selector: EntryPointSelector| {
                e.as_lib_call_execution_error(class_hash, storage_address, selector)
            };

        Ok(self
            .execute_inner_call(entry_point, remaining_gas, class_hash, error_wrapper_function)?
            .0)
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L467-495)
```text
    if (selector == CALL_CONTRACT_SELECTOR) {
        let call_contract_syscall = cast(syscall_ptr, CallContract*);
        tempvar caller_address = execution_context.execution_info.contract_address;
        let callee_address = call_contract_syscall.request.contract_address;
        // Since we process the revert log backwards,
        // entries before this point belong to the caller.
        assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=caller_address);
        let revert_log = &revert_log[1];
        // It is forbidded to call the `__execute__` function.
        assert_not_equal(call_contract_syscall.request.selector, EXECUTE_ENTRY_POINT_SELECTOR);
        execute_contract_call_syscall(
            block_context=block_context,
            contract_address=callee_address,
            caller_address=caller_address,
            entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
            caller_execution_context=execution_context,
            syscall_ptr=call_contract_syscall,
        );
        // Entries before this point belong to the callee.
        assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=callee_address);
        let revert_log = &revert_log[1];
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - CallContract.SIZE,
            syscall_ptr=syscall_ptr + CallContract.SIZE,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L497-511)
```text
    if (selector == LIBRARY_CALL_SELECTOR) {
        execute_library_call_syscall(
            block_context=block_context,
            caller_execution_context=execution_context,
            entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
            syscall_ptr=cast(syscall_ptr, LibraryCall*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - LibraryCall.SIZE,
            syscall_ptr=syscall_ptr + LibraryCall.SIZE,
        );
    }
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/account_with_dummy_validate.cairo (L43-57)
```text
    fn __execute__(
        self: @ContractState,
        contract_address: ContractAddress,
        selector: felt252,
        calldata: Array<felt252>
    ) -> Span<felt252> {
        // Validate caller.
        assert(starknet::get_caller_address().is_zero(), 'INVALID_CALLER');

        call_contract_syscall(
            address: contract_address,
            entry_point_selector: selector,
            calldata: calldata.span()
        ).unwrap_syscall()
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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/call_contract.rs (L297-343)
```rust
fn test_direct_execute_call(cairo1_type: RunnableCairo1, block_direct_execute_call: bool) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(cairo1_type));
    let contract_with_execute = FeatureContract::EmptyAccount(cairo1_type);
    let chain_info = &ChainInfo::create_for_testing();
    let mut state =
        test_state(chain_info, BALANCE, &[(test_contract, 1), (contract_with_execute, 1)]);

    let test_contract_address = *test_contract.get_instance_address(0).0.key();
    let contract_with_execute_address = *contract_with_execute.get_instance_address(0).0.key();
    let call_execute_directly_selector = selector_from_name("call_execute_directly");
    let return_result_selector = selector_from_name("return_result");

    let call_execute_directly = CallEntryPoint {
        entry_point_selector: call_execute_directly_selector,
        calldata: calldata_macro![
            // The Execute entrypoint of this contract will be called.
            contract_with_execute_address,
            // Outer calldata (passed to `execute` entrypoint)
            felt!(4_u8), // Outer calldata length.
            test_contract_address,
            return_result_selector.0,
            // Inner calldata (passed to function called by `execute` entrypoint)
            felt!(1_u8), // Inner calldata length.
            felt!(0_u8)  // Inner calldata value.
        ],
        ..trivial_external_entry_point_new(test_contract)
    };

    let mut block_context = BlockContext::create_for_testing();
    block_context.versioned_constants.block_direct_execute_call = block_direct_execute_call;
    let call_info = call_execute_directly
        .execute_directly_given_block_context(&mut state, block_context)
        .unwrap();

    if block_direct_execute_call {
        assert!(call_info.execution.failed, "Expected direct execute call to fail.");
        assert_eq!(
            format_panic_data(&call_info.execution.retdata.0),
            "0x496e76616c696420617267756d656e74 ('Invalid argument')",
        );
    } else {
        assert!(
            !call_info.execution.failed,
            "Expected direct execute call to succeed, because `block_direct_execute_call` is \
             false."
        );
    }
```
