### Title
Starknet OS `execute_replace_class` Skips Declared-Class Check Present in Blockifier, Causing Sequencer/OS State Divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo)

### Summary
The Kibana report describes a safeguard that only evaluated a partial scope (the requester's own space) instead of the full set of dependents, letting an authorized-looking action (deleting a private location) silently break state that other scopes depended on. The analogous bug class here is a safeguard ("class must be declared before use") that is enforced in one execution path (the Rust `blockifier`) but is missing in another supposedly-equivalent path (the Cairo Starknet OS), so the same transaction can be accepted with different final state depending on which path evaluates it — a scope/consistency gap identical in spirit to the Kibana issue.

### Finding Description
The `replace_class` syscall lets any deployed contract's authorized caller (i.e., the contract itself, invoked by any external caller) change which class hash a contract address points to. The Rust `blockifier`, which the sequencer uses to execute and validate transactions, explicitly requires the target class to be declared before performing the replacement: [1](#0-0) 

However, the Starknet OS's Cairo implementation of the same syscall for the deprecated (Cairo0) syscall path performs no such check at all — it simply overwrites the contract's class hash in `contract_state_changes` without verifying the class was ever declared: [2](#0-1) 

The non-deprecated Cairo syscall implementation is aware of this gap — it contains an explicit TODO acknowledging the missing check ("Check that there is a declared contract class with the given hash") but still does not perform it: [3](#0-2) 

This mirrors the Kibana root cause precisely: the "is this action safe" check (declared-class validity) is correctly scoped in one code path (blockifier execution, which decides what actually gets included/committed in a block) but is absent/under-scoped in the parallel code path (the Starknet OS, which re-executes the same transaction to produce the STARK proof of that block). Because the OS is the ground truth for proof soundness, any place where its semantics diverge from the sequencer's blockifier semantics is a correctness hazard reachable purely by an ordinary account contract invoking `replace_class` with an attacker-chosen `class_hash`.

### Impact Explanation
If a transaction that triggers `replace_class` with an undeclared class hash is processed:
- In the Rust `blockifier` (used for block building/validation), `state.get_compiled_class(request.class_hash)` fails with `StateError::UndeclaredClassHash`, causing that call/transaction to revert or fail — the sequencer will never persist a state where a contract's class hash points to an undeclared class via this path.
- In the Starknet OS (used to re-execute the block and generate the proof), the same call succeeds unconditionally and commits the new (undeclared) class hash into `contract_state_changes`.

This is a state-transition function divergence: the same transaction, applied to the same starting state, produces two different final states depending on whether it is processed by the blockifier or by the OS. This can manifest as an inability to correctly prove a block that the sequencer built (the OS trace won't match the sequencer's committed state diff/root), or — in configurations where the OS is authoritative — a contract could end up pointing at a class hash with no corresponding compiled class, breaking that contract irrecoverably (denial of contract functionality) and producing a state root that the Rust node/full-node view would never independently produce. Both outcomes fall under "wrong committed root" / "honest-node divergence," which is explicitly in scope.

### Likelihood Explanation
The trigger is trivial and requires no privilege: any account can deploy or call into a contract that invokes the `replace_class` syscall with an arbitrary, undeclared `class_hash`. This is fully within reach of "an unprivileged transaction sender, contract deployer... " per the validation rules. No special timing, mempool manipulation, or proposer collusion is required — a single, ordinary invoke transaction suffices to exercise the divergent code path.

### Recommendation
Add the same "class must be declared" check to both Cairo implementations of `execute_replace_class` (`deprecated_execute_syscalls.cairo` and `syscall_impls.cairo`) that the Rust `blockifier`'s `replace_class` syscall handler performs (`hint_processor.rs:795-807`), i.e., assert that a compiled class exists for `class_hash` in `contract_class_changes`/committed state before writing the new `StateEntry`. This should be done consistently in both the Cairo0/deprecated and the current syscall implementations so that OS re-execution semantics match blockifier semantics exactly, eliminating the possibility of a state/root divergence for this syscall.

### Proof of Concept
1. Deploy a contract `A` (e.g. any contract with a `replace_class_syscall` wrapper such as the test contract's `test_replace_class` entry point, as used in existing blockifier tests: [4](#0-3) ).
2. Send an invoke transaction calling `A.test_replace_class(class_hash=<some_never_declared_hash>)`.
3. On the sequencer (blockifier) side, the call fails with `EXECUTION_ERROR ... is not declared`, and depending on the entry-point being `__execute__`-critical, the transaction reverts or the call fails, leaving `A`'s class hash unchanged in the sequencer's committed state.
4. When the Starknet OS re-executes the identical block/transaction for proving, `execute_replace_class` in `deprecated_execute_syscalls.cairo` (or `syscall_impls.cairo`) performs the state update unconditionally, setting `A`'s class hash to the undeclared value with no error — producing a different final state/state diff than the one the sequencer committed.
5. This mismatch between the sequencer's committed state and the OS's re-executed state is the observable divergence (wrong committed root / inability to produce a valid proof matching the block).

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
