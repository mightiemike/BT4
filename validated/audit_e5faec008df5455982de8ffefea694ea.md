### Title
OS `execute_replace_class` skips the "class must be declared" check enforced by Blockifier, allowing undeclared/zero class hashes into committed state - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The `ReplaceClass` syscall lets a contract change its own class hash. Blockifier's Rust implementation validates that the target `class_hash` is actually declared (and is a Cairo1 class) before mutating state. The Starknet OS's Cairo re-implementation of the same syscall (used to build/verify the committed state and block hash) omits this check entirely — it is explicitly marked with a `TODO` and simply overwrites the contract's `class_hash` in `contract_state_changes` with whatever value the calldata supplies, including `0` or any undeclared value. This mirrors the reported bug class: an address/hash-like parameter that reaches state-mutating logic without the same validation applied elsewhere in the system, producing a divergence between the "checked" and "unchecked" code paths.

### Finding Description
In Blockifier, `replace_class` is guarded: [1](#0-0) 
It reads `get_compiled_class(class_hash)` (which errors for undeclared/zero class hashes) and further requires the class be Cairo1 before calling `set_class_hash_at`. The deprecated syscall handler enforces the same declaration check: [2](#0-1) 

In contrast, the Starknet OS Cairo implementation performs no such check. For the Cairo1 execution path: [3](#0-2) 
Note the explicit acknowledgement at line 902: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The function directly takes `request.class_hash` and writes it into `contract_state_changes` via `dict_update`, with no `assert_not_zero` or lookup against `contract_class_changes`.

The deprecated syscall variant has the identical gap: [4](#0-3) 

Both OS code paths are reachable from an ordinary contract executing `replace_class_syscall(class_hash)` (or the deprecated `replace_class` syscall) as part of a normal, unprivileged transaction — no operator/prover collusion is required to trigger the call itself.

### Impact Explanation
Blockifier (the code that actually executes transactions when building a block) will revert any `replace_class` call whose target class hash is undeclared. The Starknet OS (the code that re-executes/proves the block and derives the committed state and block hash) has no equivalent check and will accept the same call, writing an undeclared or zero class hash into the contract's state entry. This is exactly the class of bug in the reference report: a value that should be checked for a "safe" default/zero condition is instead trusted and propagated into consensus-critical state.

Because the OS output feeds directly into Patricia-tree state commitment and block hash computation, this discrepancy between the two independently-maintained implementations of the same syscall is a correctness/soundness gap in a path explicitly in scope ("Starknet OS re-execution", "state reads and aliasing", "state commitment and Patricia trees"). If a contract's class hash is committed as `0`/undeclared through this path, the contract becomes permanently unusable (denial of the affected account, i.e., funds/functionality freezing at that address), and any OS-based verification pipeline that assumes parity with Blockifier semantics is undermined, contributing to honest-node divergence between what the sequencer's execution engine would do and what the OS proves.

### Likelihood Explanation
Triggering the underlying Cairo call itself only requires a normal contract to invoke `replace_class_syscall` with an undeclared or zero `class_hash` — a call any contract deployer can include in their contract logic and any user can then invoke via a standard transaction. The missing-check code path is unconditionally reached whenever `execute_replace_class` (either variant) is executed, with no additional preconditions.

### Recommendation
Add the same declaration check present in Blockifier to both OS implementations of `execute_replace_class`: look up `class_hash` in `contract_class_changes` (and any needed persistent class-hash dictionary) and fail/assert if it is not declared (mirroring `assert_not_zero(compiled_class_hash)` patterns already used elsewhere in this file for `execute_declare_transaction`), removing the outstanding `TODO` at `syscall_impls.cairo:902` and applying an equivalent fix in `deprecated_execute_syscalls.cairo`.

### Proof of Concept
1. Deploy a Cairo1 contract exposing an entry point that calls `replace_class_syscall(class_hash)` with an attacker-supplied `class_hash` argument (e.g. the test contract's `test_replace_class`, see `crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo:200-203`).
2. Invoke it with `class_hash = 0` (or any undeclared class hash).
3. In Blockifier's actual execution, the call reverts with "is not declared" (as tested in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`).
4. Independently trace the same syscall through the OS's `execute_replace_class` (`syscall_impls.cairo:881-920`): no assertion checks `contract_class_changes` for the supplied `class_hash`, so the OS accepts the write and updates `contract_state_changes` with `class_hash = 0`, diverging from Blockifier's rejection of the identical input.

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
