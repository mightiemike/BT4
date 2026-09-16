## Finding

### Title
Starknet OS `execute_replace_class` skips the "class must be declared" check enforced by Blockifier, causing sequencer/OS state-transition divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The reported bug class is a mismatch between two execution environments that are expected to process the exact same operation identically (EVM vs. zkEVM opcodes for `CREATE`/proxy cloning). The sequencer repo has a direct analog: the `replace_class` syscall is validated differently by Blockifier (the Rust execution engine that actually builds blocks) than by the Starknet OS Cairo program (the engine that re-executes the block to produce the provable state transition and committed root).

### Finding Description
In Blockifier, `replace_class` explicitly verifies that the target class hash is declared and of the correct (Cairo1/V1) type before mutating state: [1](#0-0) 

The Cairo0 (deprecated) syscall path in Blockifier performs an equivalent check by reading the compiled class before allowing the replacement: [2](#0-1) 

In contrast, the Starknet OS's Cairo implementation of the same syscall for Cairo1 contracts explicitly documents that this check is *not* performed: [3](#0-2) 

The comment on line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms the OS blindly trusts the hint-provided `state_entry` and writes the new `class_hash` into `contract_state_changes` without verifying the class was ever declared. The Cairo0 OS path (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) has the identical gap: [4](#0-3) 

The Starknet OS is the component that produces the canonical, provable state diff and ultimately the committed state root / block hash that is verified on L1 (via `state_update` and `get_block_os_output_header` in `os.cairo`): [5](#0-4) 

Because Blockifier and the OS implement different validation rules for the same syscall, any code path where the OS's hint-driven state transition is not perfectly constrained to mirror Blockifier's actual execution (e.g. transaction proving / re-execution flows that consume OS hints derived from execution traces rather than re-deriving them from Blockifier's authoritative checks) can accept a `replace_class` to an undeclared (non-existent) class hash. This is exactly the "different execution engines don't support/validate the same primitive the same way" bug class described in the source report (EIP-1167 clone bytecode behaving differently under zkSync's execution semantics vs. standard EVM).

### Impact Explanation
If the OS's proof of a block's state transition can be produced without enforcing that a replaced class is actually declared, a contract's `class_hash` slot in the committed state tree can be inconsistent with reality (pointing to a class that was never declared and has no CASM/Sierra body available). This can:
- Produce a permanently broken/frozen contract (all future calls to that address fail since no executable class exists for the recorded hash), and
- Create a divergence between the sequencer's own execution result (which would revert such a transaction) and what the OS considers a valid state transition, undermining the guarantee that the OS re-execution proves the same state transition that Blockifier computed.

This falls into "wrong committed root" / "honest-node divergence" / "permanent freezing" categories.

### Likelihood Explanation
Reachable by any unprivileged transaction sender: `replace_class` is exposed to ordinary Cairo1 contracts via `starknet::replace_class_syscall`, callable from any `__execute__`/external entry point with a caller-supplied `class_hash` argument. The missing check is explicitly acknowledged by a `TODO` comment in the shipped OS program, meaning the gap is confirmed, not speculative, and lives directly in the syscall execution path exercised on every block that includes a `replace_class` call.

### Recommendation
Add the same "class hash must be declared" (and CASM-compatibility) check inside the Starknet OS's `execute_replace_class` (both `syscall_impls.cairo` for Cairo1 and `deprecated_execute_syscalls.cairo` for Cairo0) that Blockifier already enforces in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class`, e.g. by guessing/asserting the compiled-class fact for the given class hash exists in the OS's `contract_class_changes`/global class-hash dictionary before writing the new `StateEntry`, matching Blockifier's `get_compiled_class` check and `is_cairo1` requirement.

### Proof of Concept
1. Declare (or don't declare) a class hash `H` that is never actually declared on-chain.
2. Deploy a Cairo1 contract exposing `replace_class_syscall`.
3. Invoke `replace_class_syscall(H)` from the deployed contract, where Blockifier's `syscall_base.rs::replace_class` would reject the call with "is not declared" (as covered by the existing test at `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`), while the OS's `execute_replace_class` (`syscall_impls.cairo:881-920`) has no equivalent guard and would accept the state mutation if fed a hint claiming a matching `StateEntry`, per the acknowledged `TODO` at line 902.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L221-252)
```text
    // Execute transactions.
    let outputs = initial_carried_outputs;
    with contract_state_changes, contract_class_changes, outputs {
        execute_transactions(block_context=block_context);
    }
    let final_carried_outputs = outputs;

    // Update the state.
    %{ EnterScopeWithAliases %}
    let (squashed_os_state_update, state_update_output) = state_update{hash_ptr=pedersen_ptr}(
        os_state_update=OsStateUpdate(
            contract_state_changes_start=contract_state_changes_start,
            contract_state_changes_end=contract_state_changes,
            contract_class_changes_start=contract_class_changes_start,
            contract_class_changes_end=contract_class_changes,
        ),
        should_allocate_aliases=should_allocate_aliases(),
    );
    %{ vm_exit_scope() %}

    // Write the OS block output.
    let os_output_header = get_block_os_output_header(
        block_context=block_context,
        state_update_output=state_update_output,
        os_global_context=os_global_context,
    );
    assert os_output_per_block_dst[0] = OsOutput(
        header=os_output_header,
        squashed_os_state_update=squashed_os_state_update,
        initial_carried_outputs=initial_carried_outputs,
        final_carried_outputs=final_carried_outputs,
    );
```
