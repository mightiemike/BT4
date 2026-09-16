### Title
Direct `__execute__` invocation protection is bypassable via `library_call` syscall - (File: `crates/blockifier/src/execution/syscalls/hint_processor.rs`, `crates/blockifier/src/execution/native/syscall_handler.rs`, `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`, `crates/apollo_starknet_os_program/.../execution/syscall_impls.cairo`, `crates/apollo_starknet_os_program/.../execution/deprecated_execute_syscalls.cairo`)

### Summary
The sequencer deliberately blocks any contract from directly invoking another contract's `__execute__` entry point via the `call_contract` syscall, gated by the `block_direct_execute_call` versioned constant. However, this same check is not applied to the `library_call` syscall, which also allows an arbitrary caller-supplied selector to be executed. This is directly analogous to the reported `LSSVMPair.call()` issue: a powerful, generic "call with attacker-chosen selector" primitive is only partially filtered against a specific dangerous selector, leaving an equivalent code path unguarded.

### Finding Description
`call_contract` syscall handlers explicitly reject calls whose `function_selector` equals `__execute__` when `block_direct_execute_call` is enabled: [1](#0-0) [2](#0-1) [3](#0-2) 

The Cairo OS implementation for `CALL_CONTRACT` performs the analogous check: [4](#0-3) [5](#0-4) 

However, the `library_call` handling path, which also lets any calling contract pass an arbitrary `function_selector` to invoke code (with `CallType::Delegate` semantics), performs no such filtering in any of the four call sites that implement it:

- Cairo1/VM path — `crates/blockifier/src/execution/syscalls/hint_processor.rs::library_call` (lines 631-663) constructs the `CallEntryPoint` directly from `request.function_selector` with no call to `maybe_block_direct_execute_call`. [6](#0-5) 
- Native path — `crates/blockifier/src/execution/native/syscall_handler.rs::library_call` (lines 441-485) similarly omits the check that its sibling `call_contract` (lines 486-547 in the same file) performs. [7](#0-6) 
- Deprecated Cairo0 path — `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::execute_library_call` (lines 898-929) also omits the check. [8](#0-7) 
- OS Cairo1 path — `execute_library_call` in `syscall_impls.cairo` (lines 241-282) has no selector filter, unlike `execute_call_contract` right above it. [9](#0-8) 
- OS Cairo0 (deprecated) path — the `LIBRARY_CALL_SELECTOR` branch in `deprecated_execute_syscalls.cairo` (lines 497-510) has no equivalent `assert_not_equal` against `EXECUTE_ENTRY_POINT_SELECTOR`, unlike the `CALL_CONTRACT_SELECTOR` branch immediately above it (lines 467-495). [10](#0-9) 

This mirrors the external report's pattern exactly: a single generic/dangerous call primitive (`call()` in the report, `call_contract`/`library_call` here) needs to filter dangerous selectors (`pairTransferERC20From()` etc. in the report, `__execute__` here), but the filter was applied inconsistently to only one of the equivalent code paths.

### Impact Explanation
`block_direct_execute_call` exists specifically to prevent a contract from invoking `__execute__` outside of the intended transaction-level flow (which is protected by nonce/signature validation, fee charging, and revert-log bookkeeping performed in `execute_invoke_function_transaction`). Regression/unit tests exist precisely to enforce this invariant for `call_contract`, confirming the security team treats bypassing this restriction as a defect worthy of dedicated coverage: [11](#0-10) [12](#0-11) 

Because `library_call` is reachable from any Cairo contract executed as part of a normal INVOKE transaction (a plain external call chain, not requiring any privileged role), an unprivileged transaction sender can craft a contract that calls `library_call_syscall(class_hash, selector!("__execute__"), calldata)` to re-enter the `__execute__` logic of any declared class, bypassing the guard that is explicitly enforced for `call_contract`. This creates divergent, unintended entry-point semantics between the two call primitives and undermines an explicit security control (`block_direct_execute_call`) that the blockifier/OS otherwise treat as mandatory. Since `library_call` executes with `CallType::Delegate` (caller's own storage context), the direct impact is scoped to "borrowing" another class's `__execute__` code with the caller's own storage — but this still defeats the protocol-level invariant that `__execute__` should only run as the top-level, validated action of an account, and OS/blockifier could diverge in behavior from what they assert is guaranteed (unauthorized account action / control-flow bypass), matching the "medium risk" class of the original report.

### Likelihood Explanation
This is trivially reachable: any account can submit a normal INVOKE transaction that calls a contract which issues `library_call_syscall` with an attacker-chosen selector and class hash — no special privileges, staking, or proposer/validator role required. The gap is present across all current syscall handler implementations (Cairo1 VM, Cairo Native, Cairo0 deprecated) and the OS-side Cairo implementations, so it's not an incidental oversight in a single execution path but a structural inconsistency in how the `block_direct_execute_call` protection is applied.

### Recommendation
Apply the same `block_direct_execute_call` / `EXECUTE_ENTRY_POINT_SELECTOR` filtering used in `call_contract` to all `library_call` code paths:
- `crates/blockifier/src/execution/syscalls/hint_processor.rs::library_call`
- `crates/blockifier/src/execution/native/syscall_handler.rs::library_call`
- `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::execute_library_call`
- `crates/apollo_starknet_os_program/.../execution/syscall_impls.cairo::execute_library_call`
- `crates/apollo_starknet_os_program/.../execution/deprecated_execute_syscalls.cairo` (`LIBRARY_CALL_SELECTOR` branch)

by invoking `maybe_block_direct_execute_call` (Rust side) or the equivalent `assert_not_equal(selector, EXECUTE_ENTRY_POINT_SELECTOR)` (Cairo OS side) before executing the delegated call, mirroring the `call_contract` implementation exactly.

### Proof of Concept
Not fully verifiable without execution environment access, but the logical PoC is:
1. Deploy/declare an account class `A` with a standard `__execute__(calls)` entry point.
2. Deploy a contract `B` (or reuse the test feature contract) whose external function calls `library_call_syscall(class_hash_of(A), selector!("__execute__"), calldata)`.
3. Submit a normal INVOKE transaction from any unprivileged account calling `B`'s function, with `calldata` crafted as `A`'s `__execute__` expects (e.g., an array of `Call`s).
4. Observe that the call succeeds (executing `A`'s `__execute__` logic under `B`'s storage context) even though `block_direct_execute_call = true`, whereas the equivalent attempt via `call_contract_syscall(A, selector!("__execute__"), calldata)` is rejected with `DirectExecuteCall`/`Invalid argument`, as shown by the existing regression tests: [13](#0-12)

### Citations

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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L441-485)
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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L516-519)
```rust
        let selector = EntryPointSelector(entry_point_selector);
        self.base
            .maybe_block_direct_execute_call(selector)
            .map_err(|e| self.handle_error(remaining_gas, e))?;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L191-194)
```text
    if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L467-510)
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
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L631-663)
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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/call_contract.rs (L277-344)
```rust
#[cfg_attr(
    feature = "cairo_native",
    test_case(
    RunnableCairo1::Native, true;
    "Call execute directly using native, `block_direct_execute_call` = true."
))]
#[cfg_attr(
    feature = "cairo_native",
    test_case(
    RunnableCairo1::Native, false;
    "Call execute directly using native, `block_direct_execute_call` = false."
))]
#[test_case(
    RunnableCairo1::Casm, true;
    "Call execute directly using VM, `block_direct_execute_call` = true."
)]
#[test_case(
    RunnableCairo1::Casm, false;
    "Call execute directly using VM, `block_direct_execute_call` = false."
)]
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
}
```

**File:** crates/starknet_os_flow_tests/src/tests.rs (L2737-2775)
```rust
/// Verify OS blocks direct calls to `__execute__` entry point.
#[rstest]
#[tokio::test]
async fn test_direct_execute_call() {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm));
    let dummy_account =
        FeatureContract::AccountWithoutValidations(CairoVersion::Cairo1(RunnableCairo1::Casm));
    let (mut test_builder, [test_contract_address, dummy_account_address]) =
        TestBuilder::create_standard_with_config(
            [
                (test_contract, default_test_contract_constructor_calldata()),
                (dummy_account, calldata![]),
            ],
            TestBuilderConfig { use_kzg_da: true, ..Default::default() },
        )
        .await;

    let calldata = create_calldata(
        test_contract_address,
        "test_direct_execute_call",
        &[
            **dummy_account_address,
            Felt::from(5), // Outer calldata length.
            **test_contract_address,
            selector_from_name("assert_eq").0,
            Felt::TWO, // Inner calldata length.
            Felt::ONE, // Arg 1.
            Felt::ONE, // Arg 2.
        ],
    );
    test_builder.add_funded_account_invoke(invoke_tx_args! { calldata });

    // Run test.
    let test_output = test_builder.build_and_run().await;
    test_output.perform_default_validations();
    test_output.assert_storage_diff_eq(test_contract_address, HashMap::default());
    test_output.assert_storage_diff_eq(dummy_account_address, HashMap::default());
    test_output.expect_hint_coverage("test_direct_execute_call");
}
```
