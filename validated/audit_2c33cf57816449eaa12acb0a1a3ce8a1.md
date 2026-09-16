### Title
Starknet OS `execute_replace_class` syscall lacks the "class is declared" check enforced by Blockifier, causing state-root divergence — (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
In blockifier (the sequencer's real transaction-execution engine), the `replace_class` syscall handler explicitly verifies the requested class hash is a declared class before mutating a contract's `class_hash` entry: [1](#0-0) . If the class is not declared, `get_compiled_class` returns an error and the syscall (and thus the calling scope) reverts — this is directly confirmed by the dedicated test asserting `"is not declared"` on an undeclared class hash [2](#0-1) .

The Starknet OS's own implementation of the same syscall, used to independently re-execute/verify the block for proof generation, performs no such check. The code contains an explicit acknowledgment of the gap:

```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [3](#0-2) 

The older/deprecated Cairo0 OS syscall path has the identical omission: [4](#0-3) .

### Finding Description
`replace_class` is a standard, unprivileged Starknet syscall — any deployed contract can invoke it against its own storage address during normal `__execute__`/entry-point execution, with no admin/owner check required by protocol design. The only safety invariant Starknet relies on is that the target class hash must already be a declared class; this is what blockifier enforces at execution time via `state.get_compiled_class(request.class_hash)?` before committing the state mutation.

The Starknet OS is the canonical re-execution engine used to independently replay a block's transactions and produce the STARK proof that attests the committed state transition is correct (it recomputes `contract_state_changes` from the same transaction inputs that blockifier already executed). Because the OS's `execute_replace_class` never verifies the class is declared, it will unconditionally accept a `replace_class` call with any `class_hash`, including one that was never declared. This means the OS's model of "does this syscall succeed or revert" diverges from blockifier's model: blockifier reverts the calling scope (and rolls back its associated state changes) whenever the class hash is undeclared, whereas the OS applies the state entry write unconditionally and continues.

### Impact Explanation
If a code path or execution nuance can cause blockifier to revert an inner call that invoked `replace_class` with an undeclared class hash (which is the intended, enforced behavior), the state change from that call must NOT be part of the block's final `contract_state_changes`/state commitment. But because the OS's re-execution does not replicate the same revert-triggering condition, the OS could compute a different final state — i.e., a state entry for that contract with the undeclared class hash — diverging from the actual state blockifier committed. This constitutes an honest-node execution-engine divergence / wrong computed state root between the sequencer's committed state and the Starknet OS's re-executed/proven state, which is exactly the class of impact called out as valid (wrong committed root, honest-node divergence).

### Likelihood Explanation
The bug is a straightforward, unconditional logic omission (not a race condition or op mistake) documented by the authors themselves via the TODO comment, present in both the current (`syscall_impls.cairo`) and deprecated (`deprecated_execute_syscalls.cairo`) OS syscall implementations. Any transaction (from an unprivileged sender or unprivileged contract) that triggers a `replace_class` syscall with an undeclared class hash inside a code path where blockifier's revert semantics and the OS's replay diverge is sufficient to trigger this discrepancy; no elevated privileges, staking, or special network position are required to construct such a transaction.

### Recommendation
Add the missing "class is declared" verification to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring the check already performed in blockifier's `replace_class` handler (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`), and ensure the OS aborts/reverts the encompassing call in the same manner blockifier does, so re-execution/proof generation remains consistent with the sequencer's actual committed state transitions.

### Proof of Concept
Not independently reproducible from static analysis alone (would require constructing a Cairo1/Cairo0 contract that calls `replace_class_syscall` with an undeclared class hash inside a scope whose surrounding revert-handling differs between blockifier and OS replay, and running it through both the blockifier test harness — confirmed to reject via `"is not declared"` [2](#0-1)  — and the Starknet OS replay/re-execution pipeline, which per the code's own TODO performs no such check [3](#0-2) ). This gap is uncertain in terms of end-to-end exploitability without further tracing of exactly which call-revert boundaries in blockifier are and are not observed by the OS's replay logic; flagging this uncertainty explicitly since a full confirmation would require running the OS re-execution test suite (e.g. `crates/starknet_os_flow_tests`) against a crafted transaction, which was not performed here.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-391)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-920)
```text
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
