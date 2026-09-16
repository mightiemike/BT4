### Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes OS/blockifier execution divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The blockifier's Rust syscall implementation of `replace_class` validates that the target class hash is declared (and is a Cairo1 class) before mutating a contract's class hash, and rejects the call otherwise. The Starknet OS's own Cairo implementation of the same syscall (used to independently re-execute/prove blocks) skips this validation entirely, as explicitly marked by an unresolved TODO in the code.

### Finding Description
In the blockifier (the engine actually used by the sequencer to execute transactions and decide on-chain results), `replace_class` enforces two checks before updating state: [1](#0-0) 

The same enforcement exists for the deprecated (Cairo 0) syscall path: [2](#0-1) 

However, the Starknet OS's Cairo implementation of the identical syscall — used for independent re-execution/proving of the block (Starknet OS re-execution) — performs no such check. The `execute_replace_class` function in `syscall_impls.cairo` directly writes the new (unvalidated) `class_hash` into `contract_state_changes`, with an explicit TODO acknowledging the missing check: [3](#0-2) 

The same missing validation exists in the deprecated syscall dispatch path used for Cairo 0 contracts: [4](#0-3) 

Both OS entry points are reached directly from an unprivileged contract's `replace_class` syscall call during normal transaction execution (`REPLACE_CLASS_SELECTOR` dispatch in `execute_syscalls.cairo` and `execute_deprecated_syscalls`): [5](#0-4) [6](#0-5) 

As a result, a contract calling `replace_class` with an undeclared class hash (or, in the non-deprecated syscall, a Cairo0 class hash) is correctly rejected/reverted by the blockifier, but the OS re-execution path accepts it unconditionally and commits the unvalidated class hash into `contract_state_changes`.

### Impact Explanation
Since the Starknet OS is the component that re-executes transactions to independently derive/prove the resulting state diff, a discrepancy between the OS's acceptance criteria and the blockifier's acceptance criteria for `replace_class` means the OS can compute a different execution outcome (accepted vs. reverted, and a different resulting class hash for the contract) than what the blockifier actually committed on-chain. This constitutes honest-node/execution-engine divergence and can lead to a wrong committed state root being derived during OS-based verification/proving, or the OS being unable to correctly reproduce the sequencer's committed state (freezing/failing block proving).

### Likelihood Explanation
Likelihood is high: any contract can trivially trigger this by calling `replace_class` with an arbitrary, undeclared class hash felt as part of a normal, unprivileged transaction — no special privileges are required, and the code path is a standard syscall available to all Cairo1 and Cairo0 contracts.

### Recommendation
Add the same declared-class (and Cairo version) validation in the OS's `execute_replace_class` implementations (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that exists in the blockifier's `syscall_base.rs::replace_class` and `deprecated_syscalls/hint_processor.rs::replace_class`, i.e., verify the class hash is declared (via `get_compiled_class`/equivalent state read) and reject Cairo0 replacements for Cairo1 contracts, before committing the state update, resolving the `TODO(Yoni, 1/1/2026)` comment.

### Proof of Concept
1. Deploy a Cairo1 contract exposing `replace_class`.
2. Call it with an arbitrary undeclared felt as `class_hash` (as in the blockifier test `undeclared_class_hash` at `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`, which shows the blockifier correctly errors with "is not declared").
3. During block execution (blockifier), this transaction reverts / fails as shown by the test.
4. During OS re-execution of the same call sequence via `execute_replace_class` in `syscall_impls.cairo:881-920`, the OS performs no declared-class check and would write the state update to `contract_state_changes` unconditionally, diverging from the blockifier's rejection.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
    }
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L795-807)
```rust
    fn replace_class(
        request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<ReplaceClassResponse> {
        // Ensure the class is declared (by reading it).
        syscall_handler.state.get_compiled_class(request.class_hash)?;
        syscall_handler
            .state
            .set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;

        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L881-920)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L676-688)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(
            contract_address=execution_context.execution_info.contract_address,
            syscall_ptr=cast(syscall_ptr, ReplaceClass*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - ReplaceClass.SIZE,
            syscall_ptr=syscall_ptr + ReplaceClass.SIZE,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L197-205)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
