### Title
Starknet OS `execute_replace_class` omits declared-class check present in Blockifier, allowing state-root divergence from an unprivileged `replace_class` syscall - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
Any contract can invoke the `replace_class` syscall with an arbitrary, undeclared class hash. Blockifier (the Rust execution engine that actually runs transactions when the sequencer builds a block) rejects this by verifying the class is declared before writing the new class hash. The Starknet OS's Cairo implementation of the same syscall, used to re-execute the block for proving, performs the class-hash state update unconditionally, with an explicit `TODO` acknowledging the missing check.

### Finding Description
The `ReplaceClass` syscall changes the class hash bound to the calling contract's storage address. In Blockifier, both the deprecated (Cairo0) and current syscall handlers enforce that the target class hash is actually declared before mutating state: [1](#0-0) 

which is exercised in tests expecting an explicit `"is not declared"` failure for an undeclared class hash: [2](#0-1) 

In the Starknet OS Cairo program, both the deprecated and the current `execute_replace_class` implementations perform the equivalent `dict_update` on `contract_state_changes` to swap in the new `class_hash` **without ever checking that the class hash corresponds to a declared class**: [3](#0-2) 

The missing check is explicitly called out with `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` at line 902. The deprecated (Cairo0) syscall path has the identical gap: [4](#0-3) 

Because the OS is the component that re-executes the block to produce the proof that determines the committed state root and block hash, any semantic difference between what Blockifier actually does (reject/revert on an undeclared class hash) and what the OS Cairo program does (unconditionally accept and commit the class-hash change) is a correctness bug: the OS's contract state changes / class-hash tree entries can end up different from what the real block execution produced, or the OS can silently "succeed" a state mutation that should have failed and reverted the transaction.

### Impact Explanation
This is reachable from a single unprivileged transaction: any contract (or a bare test/attack contract deployed by anyone) simply needs to call the `replace_class` syscall with a felt that is not a declared class hash. Since the OS's `execute_replace_class` has no validation, it will:
- Commit an arbitrary/garbage class hash into `contract_state_changes` for the calling contract's address, changing the leaf that will be included in the contract state Patricia tree and therefore the final state commitment/block hash computed by the OS.
- Not reject the call or trigger the same failure/revert path that Blockifier would exercise for the identical syscall in the same context, producing execution/DA and state-diff divergence between the sequencer's actual block build and the OS's proof of that block.

This falls squarely in the "wrong committed root or block hash" / "honest-node divergence" impact category: a network relying on the OS proof to attest correctness of the block could commit an incorrect state root, or the OS execution could diverge from the real (blockifier) execution outcome for the same transaction, breaking the guarantee that the proof faithfully represents the actual state transition.

### Likelihood Explanation
High likelihood of exploitability: no privileged actor is needed. The `replace_class` syscall is available to any contract's `__execute__`/entry-point code path (deprecated Cairo0 contracts and Cairo1 contracts alike), and only a single felt argument (an arbitrary/garbage class hash) is required to trigger the divergence in the OS's Cairo implementation, since the check is entirely absent rather than merely buggy.

### Recommendation
Add, in the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), a check equivalent to Blockifier's `get_compiled_class`/declared-class lookup before performing the `dict_update` on `contract_state_changes`, and make the syscall fail (matching Blockifier's error/revert semantics) when the class hash is undeclared, so that OS re-execution stays consistent with Blockifier's actual execution result for every case of `replace_class`.

### Proof of Concept
1. Deploy any contract (Cairo0 or Cairo1) that exposes a function performing `replace_class_syscall(<undeclared_class_hash>)` (e.g., using the existing `test_replace_class` test helper entry point already present in the feature-contract test fixtures, called with a class hash that was never declared).
2. Submit a normal, unprivileged `invoke` transaction calling that function with an undeclared class hash.
3. Observe that Blockifier's own test (`test_replace_class` in `deprecated_syscalls_test.rs`) confirms Blockifier rejects the call ("is not declared") — i.e., Blockifier's real block execution reverts/fails this call.
4. Trace the same syscall through the OS's `execute_replace_class` (`syscall_impls.cairo:881-920` / `deprecated_execute_syscalls.cairo:307-329`): there is no declared-class check, so the OS commits the new (bogus) class hash into `contract_state_changes` unconditionally, producing a state/class-hash change in the OS's committed state that Blockifier's real execution never actually produced for that same transaction — a concrete OS/Blockifier execution divergence.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-404)
```rust
#[test]
fn test_replace_class() {
    // Negative flow.
    let chain_info = &ChainInfo::create_for_testing();
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo0);
    let empty_contract = FeatureContract::Empty(CairoVersion::Cairo0);
    let mut state = test_state(chain_info, Fee(0), &[(test_contract, 1), (empty_contract, 1)]);
    let test_address = test_contract.get_instance_address(0);
    // Replace with undeclared class hash.
    let calldata = calldata![felt!(1234_u16)];
    let entry_point_call = CallEntryPoint {
        calldata,
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err().to_string();
    assert!(error.contains("is not declared"));

    // Positive flow.
    let old_class_hash = test_contract.get_class_hash();
    let new_class_hash = empty_contract.get_class_hash();
    assert_eq!(state.get_class_hash_at(test_address).unwrap(), old_class_hash);
    let entry_point_call = CallEntryPoint {
        calldata: calldata![new_class_hash.0],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    entry_point_call.execute_directly(&mut state).unwrap();
    assert_eq!(state.get_class_hash_at(test_address).unwrap(), new_class_hash);
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
