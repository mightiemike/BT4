## Title
`block_direct_execute_call` protection is bypassable via `library_call` syscall - (File: `crates/blockifier/src/execution/syscalls/syscall_base.rs`)

### Summary
The `GovernorAlpha` veto bug lets a malicious proposer circumvent a targeted restriction (blocking vetoes on calls to the contract itself) by reaching the same effect through a different call path the check doesn't cover. The sequencer has a structurally identical pattern: the `block_direct_execute_call` protection, which is meant to forbid contracts from directly invoking a class's `__execute__` entry point via a syscall, is enforced only on the `call_contract` syscall path and is never checked on the `library_call` (delegate-call) syscall path, even though `library_call` can invoke the exact same `__execute__` entry point selector.

### Finding Description
The guard is implemented in `SyscallHandlerBase::maybe_block_direct_execute_call`: [1](#0-0) 

It rejects a call whose `entry_point_selector` equals `selector_from_name(EXECUTE_ENTRY_POINT_NAME)` when `versioned_constants.block_direct_execute_call` is set.

This check is invoked from the native `call_contract` handler: [2](#0-1) 

and from the equivalent VM `call_contract` path in `deprecated_syscalls/hint_processor.rs`: [3](#0-2) 

However, the `library_call` syscall implementations build a `CallEntryPoint` with an arbitrary `entry_point_selector` (including `EXECUTE_ENTRY_POINT_NAME`) and never call `maybe_block_direct_execute_call` or perform any equivalent selector check: [4](#0-3) [5](#0-4) 

Both `call_contract` and `library_call` are dispatched from the same syscall table (`execute_syscall_from_selector`), confirming `library_call` is a fully reachable, unprivileged syscall from any contract during normal (`Execute`) mode: [6](#0-5) 

Because `library_call` preserves `storage_address`/`caller_address` of the calling contract (delegate-call semantics) and only changes which class's code executes, any contract can construct a `library_call` targeting any declared class's `__execute__` selector, achieving the exact call the `block_direct_execute_call` flag was designed to prevent, but through a code path the check never examines — mirroring the GovernorAlpha bug where the veto restriction was keyed to a specific narrow condition (`call to self`) instead of the actual sensitive selector, letting the attacker route around it via an unguarded path.

### Impact Explanation
If `block_direct_execute_call` is relied upon by the protocol as a security invariant (e.g., to prevent a contract from directly triggering another account's `__execute__` logic outside the normal transaction-validate/execute flow — which could be used to bypass invariants that `__execute__` is assumed to only be reachable as the outermost call of a transaction), this bypass allows any transaction sender to defeat that invariant using `library_call` instead of `call_contract`. This is a protocol-consensus-relevant path since it changes execution-engine (blockifier) behavior reachable from any submitted transaction, and any node not aware of/handling the bypass in the same way could diverge, or the intended protection meant to prevent illegitimate `__execute__` invocation is silently circumvented, undermining assumptions in account abstraction / meta-transaction handling that depend on this restriction.

### Likelihood Explanation
High: `library_call` is a standard, fully-permitted syscall available to any Cairo 1 contract in `Execute` mode, requiring no special privileges — just supplying `EXECUTE_ENTRY_POINT_NAME`'s selector and a target class hash, which is public information. No coordination with block producers or protocol-level actors is required, and the exploit is a single-transaction, single-call construction.

### Recommendation
Move the `maybe_block_direct_execute_call` check into the shared inner-call construction path (or call it explicitly from both `call_contract` and `library_call` for both the native and VM syscall handlers), so the `EXECUTE_ENTRY_POINT_NAME` selector is blocked regardless of which syscall (`call_contract` or `library_call`) is used to reach it, exactly as the original report recommends restricting checks to the specific sensitive selector rather than a narrow call-shape condition.

### Proof of Concept
1. Enable `block_direct_execute_call` (as done in current versioned constants, confirmed by matches in `crates/blockifier/src/blockifier_versioned_constants.rs` and the `blockifier_versioned_constants_0_13_x/0_14_x.json` resource files).
2. Deploy/declare any account/contract class `Target` with an `__execute__` external entry point.
3. From an attacker contract, invoke:
   `library_call_syscall(class_hash: Target, entry_point_selector: selector!("__execute__"), calldata: [...])`
   during normal `Execute` mode.
4. Observe the call succeeds and `Target.__execute__` runs with the caller's own `storage_address`/`caller_address` context, whereas the equivalent `call_contract_syscall(Target_address, selector!("__execute__"), ...)` is rejected with `DirectExecuteCall`/`INVALID_ARGUMENT_FELT` per `maybe_block_direct_execute_call`.

Note: I could not fully verify from the available index the exact downstream protocol invariant that `block_direct_execute_call` protects (its introduction rationale/versioned-constants diff commentary was only partially retrievable). If the flag's purpose is confirmed to be purely a defense-in-depth/no-consensus-impact guard rather than a security-critical invariant, this finding's severity should be reassessed accordingly — a Devin session with full file access could confirm this via the versioned-constants changelog and related tests (`syscall_tests/call_contract.rs`, `deprecated_syscalls_test.rs`).

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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L507-519)
```rust
        if self.base.context.execution_mode == ExecutionMode::Validate
            && self.base.call.storage_address != contract_address
        {
            let err = SyscallExecutorBaseError::InvalidSyscallInExecutionMode {
                syscall_name: "call_contract".to_string(),
                execution_mode: self.base.context.execution_mode,
            };
            return Err(self.handle_error(remaining_gas, err.into()));
        }
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

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L569-594)
```rust
        SyscallSelector::CallContract => {
            execute_syscall(syscall_executor, vm, selector, T::call_contract)
        }
        SyscallSelector::Deploy => execute_syscall(syscall_executor, vm, selector, T::deploy),
        SyscallSelector::EmitEvent => {
            execute_syscall(syscall_executor, vm, selector, T::emit_event)
        }
        SyscallSelector::GetBlockHash => {
            execute_syscall(syscall_executor, vm, selector, T::get_block_hash)
        }
        SyscallSelector::GetClassHashAt => {
            execute_syscall(syscall_executor, vm, selector, T::get_class_hash_at)
        }
        SyscallSelector::GetExecutionInfo => {
            execute_syscall(syscall_executor, vm, selector, T::get_execution_info)
        }
        SyscallSelector::Keccak => execute_syscall(syscall_executor, vm, selector, T::keccak),
        SyscallSelector::Sha256ProcessBlock => {
            execute_syscall(syscall_executor, vm, selector, T::sha256_process_block)
        }
        SyscallSelector::Sha512ProcessBlock => {
            execute_syscall(syscall_executor, vm, selector, T::sha512_process_block)
        }
        SyscallSelector::LibraryCall => {
            execute_syscall(syscall_executor, vm, selector, T::library_call)
        }
```
