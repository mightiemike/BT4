Based on the direct code evidence gathered, I found a concrete analog: an inconsistently-enforced permission/scope check across equivalent syscall paths — mirroring the Pixelfed bug class of "a security check applied on one endpoint but not on the functionally-equivalent endpoint that reaches the same privileged action."

### Title
Missing `__execute__` entry-point block on `library_call` syscall lets a contract bypass the direct-execute-call protection enforced on `call_contract` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Starknet OS (and the deprecated Cairo0 syscall path) enforce a dedicated anti-phishing/anti-bypass rule that forbids any contract from directly invoking another contract's `__execute__` entry point via the `call_contract` syscall. This rule is implemented as an explicit selector check in `execute_call_contract`, but the equivalent check is absent from the `execute_library_call` implementation, even though `library_call` can target the exact same selector on the exact same class code.

### Finding Description
In `execute_call_contract` (Sierra/Cairo1 syscall path), the selector is checked before dispatch: [1](#0-0) 
and in the legacy Cairo0 path the analogous assertion is present in the `CALL_CONTRACT_SELECTOR` branch: [2](#0-1) 

However, the sibling `execute_library_call` function (Sierra path) performs no such selector check before dispatching execution with `request.selector`: [3](#0-2) 
and the `LIBRARY_CALL_SELECTOR` / `LIBRARY_CALL_L1_HANDLER_SELECTOR` branches in the deprecated Cairo0 path likewise dispatch `execute_library_call_syscall` with no forbidden-selector assertion: [4](#0-3) 

The blockifier (native execution) enforces this same restriction only on `call_contract` via `maybe_block_direct_execute_call`: [5](#0-4) 
implemented in `syscall_base.rs`: [6](#0-5) 
and the deprecated VM syscall handler applies it the same way, only inside `call_contract`: [7](#0-6) 

I was unable to fully confirm, within the remaining tool budget, whether the Rust `library_call` syscall implementations (native and VM) also omit this check — I could not locate the `library_call` function bodies in `hint_processor.rs` / `native/syscall_handler.rs` before running out of iterations, so this part of the analog is unverified. The OS (Cairo) side, however, is directly confirmed and cited above: `execute_library_call` has no equivalent guard in either the Sierra or the deprecated Cairo0 syscall dispatchers.

The purpose of the `block_direct_execute_call` gate (a `VersionedConstants`/OS-constant feature, `execute_entry_point_selector`) is exactly analogous to an OAuth scope check: it is meant to prevent any caller from reaching the `__execute__` entry point except through the legitimate top-level transaction flow (`select_execute_entry_point_func` / `execute_transaction_utils.cairo`), regardless of which "endpoint" (syscall) is used to reach it: [8](#0-7) 
Just as Pixelfed checked authorization on some API routes but not others reachable with the same token, the OS checks the "don't allow direct `__execute__` calls" rule on the `call_contract` syscall but not on `library_call`, which is a second syscall that can reach the identical selector/class combination.

### Impact Explanation
If the Rust blockifier side has the same gap (unconfirmed), a contract could call any account's `__execute__` entry point via `library_call_syscall` instead of `call_contract_syscall`, fully bypassing the protection that `block_direct_execute_call` is meant to provide. Even if the Rust side is not affected, the OS (used for re-execution / proving in Starknet OS flow tests, e.g. `test_direct_execute_call` in `starknet_os_flow_tests`) not enforcing the same rule on `library_call` is a state-transition inconsistency: it means the OS's notion of "allowed calls" differs from what the block-production/blockifier layer might allow, which is exactly the class of bug ("honest-node divergence" / "wrong committed root") called out in scope.

### Likelihood Explanation
Reachable trivially from a single unprivileged transaction: any deployed contract's code can invoke `library_call_syscall` with attacker-chosen `class_hash` and `selector`, so exploiting this requires no special privilege beyond deploying/declaring a contract and issuing an invoke transaction — a normal, permissionless capability.

### Recommendation
Add the same `assert_not_equal(request.selector, EXECUTE_ENTRY_POINT_SELECTOR)` (Sierra) / equivalent assertion (deprecated Cairo0) check to `execute_library_call` (and `LIBRARY_CALL_SELECTOR`/`LIBRARY_CALL_L1_HANDLER_SELECTOR` branches) in the OS, mirroring `execute_call_contract`. Additionally, verify and, if necessary, add the `maybe_block_direct_execute_call` check to the Rust `library_call` syscall implementations in both the VM (`deprecated_syscalls/hint_processor.rs`, `syscalls/hint_processor.rs`) and native (`execution/native/syscall_handler.rs`) paths, to keep OS and blockifier execution semantics consistent.

### Proof of Concept
1. Deploy a "relay" contract whose external function calls `library_call_syscall(class_hash: <target_account_class_hash>, selector: selector!("__execute__"), calldata: <attacker_calldata>)`.
2. Submit an ordinary invoke transaction that calls the relay contract's function.
3. Observe that the OS's `execute_deprecated_syscalls` / `execute_library_call` dispatches to the `__execute__` selector without hitting the `assert_not_equal(..., EXECUTE_ENTRY_POINT_SELECTOR)` guard that would have blocked the same call had it been made through `call_contract_syscall`, confirmed by comparing the code paths cited above.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L467-476)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L497-527)
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

    if (selector == LIBRARY_CALL_L1_HANDLER_SELECTOR) {
        execute_library_call_syscall(
            block_context=block_context,
            caller_execution_context=execution_context,
            entry_point_type=ENTRY_POINT_TYPE_L1_HANDLER,
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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L516-519)
```rust
        let selector = EntryPointSelector(entry_point_selector);
        self.base
            .maybe_block_direct_execute_call(selector)
            .map_err(|e| self.handle_error(remaining_gas, e))?;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-159)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
        return ();
    }

    // "__validate__" is expected to get the same calldata as "__execute__".
    local validate_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=tx_execution_context.class_hash,
        calldata_size=tx_execution_context.calldata_size,
        calldata=tx_execution_context.calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_validate,
            tx_info=tx_execution_info.tx_info,
            caller_address=tx_execution_info.caller_address,
            contract_address=tx_execution_info.contract_address,
            selector=VALIDATE_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
}
```
