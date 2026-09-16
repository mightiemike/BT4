### Title
Missing "class is declared" check in OS `execute_replace_class` allows undeclared class hash to be committed, causing state divergence from blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall handler in the Starknet OS Cairo program accepts any `class_hash` supplied by the calling contract and unconditionally writes it into `contract_state_changes`, without verifying that the class has actually been declared. The corresponding Rust (blockifier) implementation used by the sequencer to execute transactions *does* perform this check and rejects replacement with an undeclared class hash. This mirrors the reported bug class: an operation that swaps a "registered/whitelisted" value (here, a contract's class hash, analogous to the `DestinationRegistry`'s destination mapping) is performed without validating that the replacement value is legitimate ("declared"/"whitelisted").

### Finding Description
In the OS syscall implementation, `execute_replace_class` reads `class_hash` from the syscall request and directly builds a new `StateEntry`, writing it via `dict_update`, with only a `TODO` acknowledging the missing validation: [1](#0-0) 

The comment explicitly states the gap: "TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash." No lookup against declared/compiled class hashes is performed before the state entry is mutated.

By contrast, the equivalent execution path enforced by the sequencer's blockifier (the component that actually executes and orders transactions, and whose resulting state diff is expected to match what the OS re-execution produces for proving/verification) does check that the target class hash is declared, rejecting the call with an "is not declared" error otherwise: [2](#0-1) 

The deprecated (Cairo0) OS-side syscall implementation contains the identical unchecked pattern: [3](#0-2) 

Because the Starknet OS Cairo program is the code that is re-executed (and proven) to validate/commit the block produced by the sequencer, any discrepancy between what blockifier permits during sequencing and what the OS accepts during re-execution is a correctness-critical bug: it is the OS's execution trace that ultimately determines the committed state root.

### Impact Explanation
If the OS accepts a `replace_class` call with an undeclared (or malicious/never-declared) class hash while the sequencer's blockifier execution would reject such a call, the two execution paths diverge:
- If blockifier rejects a transaction, but the OS's independent replay accepts it (or vice versa), the OS-computed state commitment for the block would not match the blockifier-computed one, resulting in an incorrect committed state root / block hash and the network being unable to reach agreement on the true state (honest-node divergence), or the OS producing invalid proofs for state changes that should never have been possible.
- A contract owner (or any account triggering `replace_class` in its own context, which requires no special privilege — any unprivileged deployed contract can invoke `replace_class` on itself) could point their own contract's class hash to an arbitrary, undeclared value in the OS-visible state, since the check that would normally block this is missing on the OS side.

This satisfies the "wrong committed root" / "honest-node divergence" impact bar for a Medium/High finding, directly paralleling how the original report's un-vetted "replacement" of a registry entry undermines a security invariant (only-declared-classes-may-be-used).

### Likelihood Explanation
`replace_class` is a standard, unprivileged Starknet syscall reachable by any deployed contract in normal execution (no special sequencer/prover/operator privilege required). The vulnerable code path is hit on every OS re-execution of a block containing a `replace_class` invocation, so the divergence is triggerable by any ordinary transaction sender who calls `replace_class` with a class hash that was never declared.

### Recommendation
Add an explicit check in `execute_replace_class` (both `syscall_impls.cairo` and the deprecated `deprecated_execute_syscalls.cairo` path) that the given `class_hash` corresponds to a declared class (e.g., verifying the compiled class hash mapping is non-zero / present, mirroring the check already performed by blockifier's rust implementation as evidenced by the `"is not declared"` error), and revert/abort the syscall if the class hash is not declared — ensuring OS re-execution and blockifier execution enforce identical validation and cannot diverge.

### Proof of Concept
1. Deploy a contract and, in the OS execution/re-execution path, invoke the `replace_class` syscall with a `class_hash` value that has never been declared (no corresponding `Declare` transaction).
2. Observe that `execute_replace_class` in `syscall_impls.cairo` (or `deprecated_execute_syscalls.cairo`) writes the new (undeclared) class hash into `contract_state_changes` unconditionally — see the TODO at line 902 confirming the missing check [4](#0-3) .
3. Compare against blockifier's `test_replace_class`, which for the same undeclared-class-hash scenario returns an execution error containing `"is not declared"` [5](#0-4) , demonstrating the behavioral divergence between the two execution engines for identical input.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L881-919)
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
