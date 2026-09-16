I found a confirmed, concrete missing-validation bug: the Starknet OS Cairo implementation of the `replace_class` syscall omits the "class is declared" check that the blockifier enforces, with an explicit TODO marking the gap.

### Title
Missing declared-class validation in `execute_replace_class` allows OS state root divergence from actual (blockifier) execution — ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The blockifier's `replace_class` syscall handler explicitly verifies that the target `class_hash` is declared before mutating a contract's class assignment: it calls `state.get_compiled_class(request.class_hash)?` prior to `set_class_hash_at`, and rejects the call with a "not declared" error otherwise [1](#0-0) , confirmed by the negative-flow test asserting `"is not declared"` [2](#0-1) . The Starknet OS's own `execute_replace_class` implementation (used during Starknet OS re-execution / state-transition proving) performs the identical state mutation but deliberately skips this check, marked by an explicit TODO: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [3](#0-2) .

### Finding Description
Any unprivileged contract (reachable by any account's `__execute__`/`__validate__` calldata, since `replace_class` is a standard, permissionless syscall available to any executing contract) can invoke `replace_class_syscall(class_hash)` with an arbitrary, undeclared `class_hash`. The root-cause asymmetry:
- **Blockifier (actual execution engine used by the sequencer/batcher):** rejects the syscall unless the class was previously declared, via `syscall_handler.state.get_compiled_class(request.class_hash)?` [4](#0-3) .
- **Starknet OS (`execute_replace_class` in the Cairo program used for re-execution/proof generation of the same block):** performs the class-hash overwrite in `contract_state_changes` directly, with no equivalent declared-class check — it is explicitly deferred via a TODO comment [5](#0-4) . The deprecated syscall path in the OS has the same unguarded pattern [6](#0-5) .

This is a direct sequencer analog of the reported LooksRare bug: the "ownership"/precondition check enforced by the primary execution path is missing in a secondary path that performs the same state mutation, letting an unprivileged caller push the system into a state that violates an invariant (class hash must reference a declared class) that the rest of the protocol assumes holds.

### Impact Explanation
If any code path (bootstrapping tools, aggregator flows, or future OS callers that don't route strictly through blockifier-validated blocks before OS re-execution) allows the OS to independently derive/accept a state diff containing a `replace_class` to an undeclared class hash, the OS would compute a different (wrong) state commitment/state root than what blockifier execution would produce or accept, since `get_class_hash_at` for that contract subsequently returns a class hash with no corresponding compiled class — an inconsistent state that downstream `get_compiled_class` reads may not reject in the same way. This breaks the core soundness guarantee that the Starknet OS proof exactly attests to blockifier-equivalent execution, i.e., an honest-node/prover divergence or a wrongly-committed state root, which is one of the accepted high-impact categories.

### Likelihood Explanation
The check is missing by design today (explicit TODO, not accidental), and the deprecated syscall implementation has the identical gap, showing this is a systemic, not one-off, omission in the OS's replace_class handling. The precondition to exploit is that some component feeds the OS a state transition performing `replace_class` to an undeclared hash without blockifier's independent validation in the same execution — the code comment implies the developers are aware this needs to be closed but has not yet been implemented.

### Recommendation
Add a declared-class-hash check inside `execute_replace_class` in `syscall_impls.cairo` (and mirror it in `deprecated_execute_syscalls.cairo`'s `execute_replace_class`) equivalent to the blockifier's `get_compiled_class` check, before performing the `dict_update` on `contract_state_changes`. This should query the OS's parallel class-hash-to-compiled-class dictionary (`contract_class_changes` or equivalent) and assert non-zero/declared, matching blockifier's enforced invariant.

### Proof of Concept
1. Any account contract calls `replace_class_syscall(class_hash)` where `class_hash` was never declared.
2. In blockifier's normal execution path (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:795-807`), this reverts because `get_compiled_class` fails for the undeclared hash.
3. If the same transaction/state-diff is instead re-executed or validated via the OS `execute_replace_class` path (`syscall_impls.cairo:881-920` or `deprecated_execute_syscalls.cairo:307-329`), no equivalent check exists, so `contract_state_changes[contract_address]` is updated to reference the undeclared class hash unconditionally — producing a state transition the OS accepts that blockifier's ordinary path would reject, demonstrating the divergence in enforced invariants between the two supposedly-equivalent execution engines.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-392)
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
