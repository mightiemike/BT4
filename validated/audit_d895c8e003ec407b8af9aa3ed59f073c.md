The strongest analog to this "incomplete revert implementation" bug class in this sequencer codebase is a validation gap between the blockifier's `replace_class` syscall handler and the Starknet OS's Cairo re-implementation of the same syscall — the OS is missing checks that the blockifier enforces, mirroring the audit finding's pattern of an incomplete guard that lets an invalid state transition slip through.

### Title
Incomplete Validation in OS `execute_replace_class` Diverges from Blockifier's Declared/Cairo1-Only Enforcement - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The blockifier's native `replace_class` syscall implementation enforces two invariants before mutating a contract's class hash: (1) the target class hash must be declared, and (2) the target class must be a Cairo v1 class (rejecting downgrade to V0). The Starknet OS's Cairo re-implementation of the same syscall, used during re-execution/proving, performs neither check, an explicit TODO in the code acknowledges the missing declared-class check.

### Finding Description
In the blockifier, `SyscallHandlerBase::replace_class` reads the compiled class (which fails with `StateError::UndeclaredClassHash` if not declared) and rejects Cairo0 targets: [1](#0-0) 

This is exercised/tested for the deprecated syscall path as well, which similarly ensures the class is declared: [2](#0-1) 

However, the Starknet OS's Cairo implementation of the same syscall (`execute_replace_class`) skips this validation entirely — it directly overwrites the contract's `class_hash` in `contract_state_changes` without checking that the class hash is declared or that it isn't a forbidden V0 downgrade. The code contains an explicit acknowledgment of this gap: [3](#0-2) 

The same omission exists in the deprecated syscalls path of the OS: [4](#0-3) 

This is structurally the same bug class as the reported issue: a critical guard ("the new value must satisfy invariant X") is enforced in one code path (blockifier `upgradeAccount`/`replace_class` equivalent) but incompletely or not enforced in the path that is supposed to mirror/re-validate it (OS re-execution, analogous to a second contract-level check that should also revert).

### Impact Explanation
The Starknet OS re-executes transactions to prove that the blockifier's state transition is correct. If the OS's `execute_replace_class` accepts state transitions (undeclared or V0 class hashes) that the blockifier itself would have rejected via `SyscallExecutionError::ForbiddenClassReplacement` or `StateError::UndeclaredClassHash`, any output produced by the OS for such a trace is unconstrained relative to production rules the blockifier is supposed to enforce. This creates a class of honest-node divergence: if the blockifier's declared-classes bookkeeping and the OS's guessed/hinted class facts ever diverge (e.g., a class removed/never truly declared reaching this code path via a hint), the OS would still compute a valid-looking committed root for a `replace_class` to an undeclared or V0 class, silently accepting an unauthorized contract-class state mutation instead of proving a revert.

### Likelihood Explanation
The OS relies on hints (`GetContractAddressStateEntry`) to build `contract_state_changes`, and unlike the blockifier's `get_compiled_class`, does not independently confirm the replacement class hash is declared/valid at the point of the syscall — it defers this to (unspecified, and per the TODO, currently absent) validation elsewhere in the pipeline. Because the check is missing specifically where the syscall is executed (the exact place where the equivalent blockifier check lives), and is called out as a known gap in the code itself (`TODO(Yoni, 1/1/2026)`), the likelihood of this incomplete validation causing a proving/execution mismatch is non-trivial once such class-hash bookkeeping edge cases occur (e.g., migrated/removed classes, or malformed hints).

### Recommendation
Add the same checks in `execute_replace_class` (and its deprecated counterpart) in the Cairo OS: (1) verify that `class_hash` corresponds to a declared class in `contract_class_changes`/OS input, and (2) enforce that the target class is Cairo v1 (reject V0), matching `SyscallHandlerBase::replace_class` in `crates/blockifier/src/execution/syscalls/syscall_base.rs`. This closes the parity gap and ensures the OS cannot produce a valid proof for a state transition the blockifier would reject.

### Proof of Concept
1. Compare `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class` (declared + Cairo1-only checks) against `crates/apollo_starknet_os_program/.../syscall_impls.cairo::execute_replace_class` (no checks, TODO acknowledging the gap) and `deprecated_execute_syscalls.cairo::execute_replace_class` (same omission).
2. Observe that both OS-side implementations update `contract_state_changes` unconditionally based on the syscall request's `class_hash`, with no lookup into declared-class facts and no V0/V1 discrimination, unlike the blockifier path exercised by `test_replace_class` in `crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs:375-405`, which explicitly asserts rejection ("is not declared") for undeclared class hashes.

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
