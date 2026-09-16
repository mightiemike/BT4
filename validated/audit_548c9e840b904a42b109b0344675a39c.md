### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by the Blockifier, allowing OS/Blockifier state divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Blockifier's `replace_class` syscall handler requires that the target class hash be a declared class before updating a contract's class hash, causing the call (and, non-reverting for L1 handlers, the transaction) to fail otherwise. The Starknet OS's Cairo implementation of the same syscall, used to re-execute transactions during proof generation, does not perform this check — it is explicitly marked with a `TODO` as unimplemented.

### Finding Description
In the Blockifier (the actual execution engine used by the sequencer to build blocks), the `replace_class` syscall handler explicitly enforces that the class must already be declared before allowing the replacement, by reading the compiled class and propagating an error if it is not declared: [1](#0-0) 

This is confirmed by tests that specifically assert an error `"is not declared"` when calling `replace_class` with an undeclared class hash: [2](#0-1) 

In contrast, the Cairo implementation of the same syscall inside the Starknet OS program — which re-executes the same transactions to generate the correctness proof of a block — performs no such check. The function directly updates the `contract_state_changes` dict with the new class hash without verifying the class was declared, and even carries an explicit TODO acknowledging the gap: [3](#0-2) 

This is the direct analog of the reported bug class ("privileged/guarded state-mutating operation missing its access-control/validation guard on one code path while present on another"): in the USSD report, `mintRebalancer`/`burnRebalancer` lacked the `onlyBalancer` check that other code paths assumed was enforced; here, the OS's `execute_replace_class` lacks the "class must be declared" check that the Blockifier enforces and that downstream reasoning (state commitment, re-execution) relies on.

### Impact Explanation
The Starknet OS's re-execution is the authoritative process used to produce the STARK proof that attests the block was executed correctly and to compute the committed state root/block hash. If a contract's `__execute__` (or constructor, or any entry point) invokes `replace_class` with a class hash that was never declared:
- The Blockifier, running in the sequencer during actual block building, will reject the call (propagating the "not declared" error), causing the transaction to revert (or the inner call to fail).
- The Starknet OS, re-executing the same transaction to build the proof, will silently succeed and commit the "replace_class" state change, updating `contract_state_changes` and computing a different resulting class hash / state diff than what the Blockifier actually committed.

This produces a genuine divergence between the sequencer's committed state (from Blockifier execution) and the OS-computed state root used for the STARK proof and L1 state commitment. This falls squarely into "wrong committed root or block hash" / "honest-node divergence" impact categories, since two honest components of the same node (execution vs. re-execution/proving) reach different results for the same transaction, potentially producing an invalid proof of a valid block, or worse, a proof that legitimizes a state transition that never actually happened on the executing side.

### Likelihood Explanation
This is trivially reachable by any unprivileged contract deployer: any contract (deployed via a normal `DEPLOY`/`DEPLOY_ACCOUNT` transaction) can call the `replace_class` syscall from its own execution context with an arbitrary, undeclared class hash as calldata. No special privileges are required — this is a standard Cairo0/Cairo1 syscall available to any contract's entry point. The condition to trigger divergence (choosing an undeclared class hash) is fully attacker-controlled and requires no race condition or timing dependency.

### Recommendation
Add the equivalent "class must be declared" check to the Starknet OS's `execute_replace_class` implementations (both the Cairo1 syscall path in `syscall_impls.cairo` and the deprecated Cairo0 path in `deprecated_execute_syscalls.cairo`), mirroring the Blockifier's behavior of reading/validating the compiled class for the given class hash before permitting the state update, and reverting/failing consistently with the Blockifier when the class is not declared.

### Proof of Concept
1. Deploy a contract with an entry point that calls the `replace_class` syscall with an arbitrary, never-declared `class_hash` value (e.g., `felt!(1234)`).
2. Submit and execute the transaction via the Blockifier (sequencer) — observe the call fails with "... is not declared", as demonstrated by the existing unit test: [2](#0-1) 
3. Re-execute the identical transaction through the Starknet OS program (`execute_replace_class` in `syscall_impls.cairo`) — since no declared-class check exists there, the syscall succeeds, and `contract_state_changes` is updated with the undeclared class hash, producing a state diff/class hash that the Blockifier never actually produced. [4](#0-3)

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-29)
```rust
#[cfg_attr(feature = "cairo_native", test_case(RunnableCairo1::Native; "Native"))]
#[test_case(RunnableCairo1::Casm; "VM")]
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
