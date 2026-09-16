### Title
Starknet OS `replace_class` Syscall Omits Declared-Class Validation Present in Blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Cairo-language Starknet OS implementation of the `replace_class` syscall writes an attacker-supplied `class_hash` into a contract's state entry without verifying that the class has actually been declared (and is a Cairo1 class), whereas the Rust `blockifier` implementation of the exact same syscall performs both checks before mutating state. This is a broken-authorization-adjacent input-validation gap analogous to the Dokploy report: a caller-controlled identifier (`class_hash`, analogous to `containerId`) is passed straight into a privileged state mutation without validating it against the required precondition (must reference a real, correctly-typed, declared class).

### Finding Description
In `blockifier`, the syscall handler enforces that the target class exists and is Cairo1 before allowing a contract to replace its own class: [1](#0-0) 

and the deprecated (Cairo0) syscall path does the same via `get_compiled_class`: [2](#0-1) 

However, the Starknet OS's own Cairo re-implementation of this syscall — which is used to independently re-execute transactions when generating the STARK validity proof for a block — skips this check entirely and unconditionally writes the caller-supplied `class_hash` into the contract's state entry: [3](#0-2) 

The code even contains an explicit acknowledgment of the missing check: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The same omission exists in the deprecated syscall dispatch path used for Cairo0 transactions: [4](#0-3) 

Because the OS is the authoritative re-execution engine used to produce the proof that attests to a block's state transition, and it must faithfully reproduce blockifier's execution semantics for the proof to be sound, this creates a discrepancy: blockifier will reject/revert a `replace_class` call targeting an undeclared (or Cairo0) class hash, while the OS's Cairo implementation will accept it and commit the resulting (invalid) state entry.

### Impact Explanation
This produces a state-transition divergence between what blockifier (the actual sequencer execution engine that determines the committed block content) enforces and what the OS accepts when re-deriving/proving that same state transition. Any account or contract can trigger `replace_class` with an arbitrary, non-existent (or deprecated Cairo0) class hash as part of an ordinary transaction. If this divergence is exercised at the boundary between block building and OS-based proving, it can result in the OS committing/proving a state root inconsistent with the semantics blockifier itself enforces — i.e., a wrong committed root / honest-node divergence, one of the explicitly accepted high-impact categories. It also permanently corrupts the invariant that every contract's `class_hash` field references a declared, valid class, which can permanently freeze the affected contract (no entry point can subsequently be resolved against a nonexistent class).

### Likelihood Explanation
The precondition is trivial: any account contract can invoke `replace_class_syscall` with a `class_hash` that has never been declared (or that references a Cairo0 class), which is a completely unprivileged, single-transaction action requiring no special permissions, roles, or prior state — directly mirroring the "no-privilege attacker, arbitrary target identifier passed unchecked into privileged operation" pattern from the reference report.

### Recommendation
Add the same validation performed by `blockifier` (`syscall_base.rs::replace_class`) to the OS's Cairo `execute_replace_class` implementations in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`: verify the class is declared (i.e., present in `contract_class_changes`/declared-classes state) and that it is a Cairo1 (non-deprecated) class, rejecting the syscall otherwise, so OS re-execution semantics match blockifier exactly.

### Proof of Concept
1. Deploy an account/contract via a normal `INVOKE`/`DEPLOY_ACCOUNT` transaction.
2. From that contract, invoke `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared on the network (or that corresponds to a Cairo0/deprecated class).
3. Observe that `blockifier`'s `syscall_base.rs::replace_class` (crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378) rejects this via `get_compiled_class`/`is_cairo1` checks.
4. Trace the equivalent OS execution path (`execute_replace_class` in crates/apollo_starknet_os_program/.../syscall_impls.cairo:881-920, and the deprecated variant in deprecated_execute_syscalls.cairo:307-329): the state entry is unconditionally updated via `dict_update` with no declared-class or Cairo1 check, confirmed by the explicit `// TODO ... Check that there is a declared contract class` comment at line 902 of syscall_impls.cairo.

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
