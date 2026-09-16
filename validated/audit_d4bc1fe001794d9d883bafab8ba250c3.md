No occurrence of `UndeclaredClassHash`/`is not declared`/`declared_contracts` anywhere in the OS Cairo sources confirms there is no later validation pass that catches undeclared classes written via `replace_class`. The class-declaration check is entirely absent from the OS's execution of this syscall.

### Title
Starknet OS `replace_class` syscall omits declared-class check present in the Blockifier, enabling honest-node/prover execution divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Rust `blockifier` (the sequencer's actual transaction executor) requires that a class be declared before `replace_class_syscall` can set it as a contract's class hash. The Starknet OS Cairo implementation of the same syscall, which re-executes transactions to produce/verify the block's state transition and commitments, does not perform this check at all — it is marked with an explicit unfinished `TODO`.

### Finding Description
In the Blockifier, `replace_class` explicitly reads the target class before writing the new class hash, causing the syscall (and the whole transaction, if not reverted otherwise) to fail when the class is undeclared: [1](#0-0) 

This behavior is exercised and asserted by tests such as `undeclared_class_hash`, which expect an "is not declared" error: [2](#0-1) 

In contrast, the Starknet OS's Cairo `execute_replace_class` (used for validate-mode / native syscall handling) unconditionally overwrites `state_entry.class_hash` with the caller-supplied `class_hash` and contains a comment acknowledging the missing check: [3](#0-2) 

The deprecated OS syscall path (`deprecated_execute_syscalls.cairo`) has the identical gap, with no declared-class verification whatsoever before the state dict update: [4](#0-3) 

A codebase-wide search of the OS Cairo sources for `UndeclaredClassHash`, `"is not declared"`, or `declared_contracts` returns no results, confirming there is no other pass in the OS that later validates that all classes referenced via `replace_class` in `contract_class_changes`/`contract_state_changes` are actually declared.

This mirrors the CVE-2021-47688 bug class: an action that mutates protected state (here, the class-hash slot of a contract) is permitted by one code path (the OS) without the verification (`get_compiled_class` / declared-class check) that gates the equivalent action in another code path (the Blockifier) — i.e., the check that should gate the write is missing/out of order in one of the two enforcement points.

### Impact Explanation
The Blockifier is what the sequencer actually runs to build blocks; it will revert a transaction that calls `replace_class_syscall` with an undeclared class hash. The Starknet OS is the program whose execution trace is proven and whose output (state root / block hash) is committed on L1. Because the OS's `execute_replace_class` does not enforce the same declared-class precondition, any code path that feeds the OS a transaction trace/hints where this check is expected to matter (e.g. differences in re-execution assumptions, hint-guessed state, or future callers of this syscall implementation that don't pre-filter via the blockifier) can cause the OS to compute a different resulting state (an undeclared class hash written into the contract's class slot) than what an honest node running the Blockifier would compute for the same transaction. This is a root-cause condition for honest-node divergence and a wrong committed state root/block hash, since the entity responsible for producing the provable state transition (the OS) does not replicate a security-relevant precondition enforced by the reference execution engine (the Blockifier).

### Likelihood Explanation
The precondition gap is unconditional and always reachable in the OS code — the `TODO(Yoni, 1/1/2026)` comment is direct proof the check was never implemented in the "virtual"/native syscall path, and the deprecated syscall path never had it either. Exploitability specifically requires a scenario where the OS's execution of this syscall is not gated beforehand by the Blockifier's identical check (e.g., differing execution paths, future refactors reusing this OS function independently, or virtual/native machine executions of the OS that don't share Blockifier's state-reading semantics). Given the explicit acknowledged TODO, this is a known, unresolved gap rather than a theoretical one.

### Recommendation
Add the same declared-class verification in the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that the Blockifier performs in `hint_processor.rs::replace_class` — i.e., verify that `class_hash` is present in `contract_class_changes` (or otherwise recorded as declared) before updating `contract_state_changes`, and fail/revert the syscall consistently with the Blockifier when it is not.

### Proof of Concept
1. Declare a contract implementing `test_replace_class` (calls `replace_class_syscall`).
2. Deploy an instance of it.
3. Invoke `test_replace_class` with an arbitrary, never-declared class hash.
4. Observe: the Blockifier reverts the transaction with "is not declared" (as validated by `undeclared_class_hash` test at [2](#0-1) ).
5. If the same transaction/state-transition were instead evaluated purely by the OS's `execute_replace_class` path — which has no equivalent check — the class-hash write would succeed unconditionally, producing a state/output divergent from the Blockifier's reverted result.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L17-29)
```rust
fn undeclared_class_hash(runnable_version: RunnableCairo1) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(runnable_version));
    let mut state = test_state(&ChainInfo::create_for_testing(), BALANCE, &[(test_contract, 1)]);

    let entry_point_call = CallEntryPoint {
        calldata: calldata![felt!(1234_u16)],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err();

    assert!(error.to_string().contains("is not declared"));
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
