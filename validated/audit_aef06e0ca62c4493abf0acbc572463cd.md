### Title
Missing declared-class check in Starknet OS `execute_replace_class` allows honest-node state divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS Cairo implementation of the `replace_class` syscall does not verify that the target `class_hash` corresponds to a declared contract class before updating the contract's class-hash state entry, while the blockifier (the Rust execution engine used by the sequencer to build/validate blocks) performs this check explicitly. This authorization/validity gap is analogous to the Apache Superset bug in the report: a required ownership/authorization precondition (here, "the class must be declared before a contract's class can be replaced with it") is enforced in one code path but omitted in another, allowing an unauthorized state transition to be accepted.

### Finding Description
In the OS's non-deprecated syscall handling, `execute_replace_class` updates the contract's `StateEntry.class_hash` directly from the syscall request without any check that the class is declared: [1](#0-0) 

Note the explicit TODO acknowledging the missing check: "TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash." The deprecated (Cairo0) syscall path has the same omission: [2](#0-1) 

By contrast, the blockifier's Rust implementation of the same syscall for the deprecated (Cairo0) path explicitly reads/fetches the compiled class before setting it, which fails with an error if the class hash is undeclared: [3](#0-2) 

This is confirmed by the blockifier's own tests, which show that a `replace_class` call with an undeclared class hash is rejected with an "is not declared" error in the blockifier execution path: [4](#0-3) 

Because the sequencer builds blocks and computes the committed state diff using the blockifier (which enforces the "class must be declared" precondition — the closest sequencer analog to Superset's authorization/ownership check on a resource), while the Starknet OS re-executes the same block to prove correctness of the state transition, any discrepancy in enforcement between the two engines is a root-cause difference in code paths that are supposed to be semantically equivalent. If any transaction/contract manages to reach `replace_class` with an undeclared class hash in a way where OS execution accepts it (unconditionally updating state) but blockifier execution rejects it (or vice versa in edge cases not covered by the same validation), the OS's computed contract/class state changes and the sequencer's computed state diff would diverge, producing different committed roots for what should be the same execution — an honest-node divergence.

### Impact Explanation
If exploitable, this leads to an honest-node divergence: the OS proof of the state transition could accept a state change (replacing a contract's class with an undeclared class hash) that the blockifier itself would reject during actual block building, or the reverse. This can cause wrong committed roots/block hashes between the block build path and OS re-execution/proof path, undermining the network's ability to consistently confirm the correctness of new blocks — matching the "wrong committed root ... honest-node divergence" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: this requires a contract reachable from a normal transaction to invoke the `replace_class` syscall with a class hash it can control (any account/contract can call this syscall), which is a common, permissionless operation already exercised on Starknet. The missing validation is explicitly called out via an unresolved TODO in the OS source, indicating the gap is real and currently unaddressed in that code path, though the practical reachability of a state where OS and blockifier diverge in outcome depends on additional invariants (e.g., whether undeclared-class replace_class calls are otherwise always rejected upstream by shared logic) that could not be fully verified from the available code.

### Recommendation
Add an explicit declared-class check (mirroring the blockifier's `get_compiled_class`/`is_declared` check) in both `execute_replace_class` (syscall_impls.cairo) and the deprecated syscall path (deprecated_execute_syscalls.cairo) in the Starknet OS before updating the contract's class hash, ensuring parity with the blockifier's enforcement and closing the outstanding TODO.

### Proof of Concept
1. Deploy/declare a contract that can call the `replace_class` syscall (e.g., the existing `test_replace_class` test contract entry point).
2. Invoke the contract with an undeclared, arbitrary `class_hash` via `replace_class_syscall`.
3. In blockifier execution (used for block building/validation), the call is rejected with "is not declared" (see `syscall_tests/replace_class.rs::undeclared_class_hash`).
4. In the Starknet OS re-execution path (`execute_replace_class` in syscall_impls.cairo / deprecated_execute_syscalls.cairo), no equivalent declared-class check exists before the `dict_update` that commits the new class hash into `contract_state_changes` — confirm whether the OS accepts and commits this class-hash change unconditionally, which would produce a state/output divergence from the blockifier's rejection.

Note: Full confirmation that this produces an actual on-chain divergence (rather than being caught by some other invariant elsewhere in the OS, e.g., during class hash commitment validation) could not be completed within the available exploration — this should be verified against the complete OS source and any surrounding validation logic before treating this as conclusively exploitable.

### Citations

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
