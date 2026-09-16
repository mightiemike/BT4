Based on my research, I found a credible, in-scope analog: a validation-gap divergence between the **blockifier's** `replace_class` syscall implementation (which enforces that the target class is declared) and the **Starknet OS's** Cairo re-implementation of the same syscall (which explicitly skips that check, per an in-code `TODO`). This is reachable by any account contract simply calling the `replace_class` syscall, and it maps to the same underlying bug class as the report — an entity/state mutation is allowed to proceed without validating a precondition that governs whether the change should be permitted, causing the final committed state to diverge from what should have happened. [1](#0-0) [2](#0-1) 

### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by blockifier, causing OS/sequencer state divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The `replace_class` syscall is reachable by any contract executing arbitrary Cairo code triggered by a normal `INVOKE` transaction from an unprivileged sender. Blockifier's Rust implementation of this syscall requires the target class hash to already be declared (by attempting to read its compiled class, which errors if undeclared) before mutating `class_hash_at` for the calling contract. The Starknet OS's own Cairo re-implementation of the identical syscall, executed during OS re-execution/proving, performs the class-hash mutation unconditionally and carries an explicit `TODO` acknowledging the missing check.

### Finding Description
In blockifier, `replace_class` is validated before the state is mutated: [1](#0-0) 
This enforces that a class hash cannot become the active class of a contract unless it was previously declared on-chain. Failure to satisfy this precondition causes the syscall/entry point to error, which propagates as a reverted call/transaction in the block that is actually built and committed by the sequencer.

The OS's own Cairo implementation of the same syscall, used during Starknet OS re-execution (the process that re-derives the state diff/commitment for proving), does not perform this check: [2](#0-1) 
Note the `TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The deprecated syscalls path has the identical gap: [3](#0-2) 

By contrast, the OS's `deploy_contract` function does perform equivalent preconditions before mutating state (`assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH; assert state_entry.nonce = 0;`), showing that the pattern of "assert precondition before applying the state mutation" is the established convention in this code, but it is missing specifically for `replace_class`. [4](#0-3) 

### Impact Explanation
If a contract calls `replace_class` with a class hash that is not declared, blockifier will reject/revert that call during actual block execution and sequencing, so the state diff actually committed to L1/L2 will not reflect the class change. When the Starknet OS later re-executes the same transaction to produce a validity proof of the state transition, its Cairo implementation of `replace_class` unconditionally accepts the class hash and writes the mutated `class_hash` into `contract_state_changes`, without asserting the class was declared. This creates a genuine risk of honest-node divergence: the state diff/trace independently derived by the OS during proving does not match the state diff actually produced and committed by the sequencer's blockifier execution for the same transaction. This maps to the "wrong committed root or block hash, honest-node divergence" acceptance criterion, and is analogous to the original report's core issue — a mutation is applied against an entity without re-validating a precondition that should gate it, leading to an inconsistent final state.

### Likelihood Explanation
This is trivially reachable: any account or contract can invoke `replace_class_syscall(class_hash)` with an arbitrary/undeclared class hash from ordinary Cairo1 or Cairo0 contract code, e.g. as demonstrated by the test helper `test_replace_class` exposed on feature contracts. [5](#0-4) [6](#0-5) 
No special privileges, staking, proposer/operator role, or network conditions are required — a single unprivileged transaction is sufficient to hit this divergent code path in the OS during subsequent re-execution/proving of that transaction.

### Recommendation
Add the same "class hash must be declared" precondition check to the OS's Cairo `execute_replace_class` implementations (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that blockifier already enforces in `hint_processor.rs`, resolving the outstanding `TODO(Yoni, 1/1/2026)`. The check should assert existence of a compiled/declared class entry for `class_hash` prior to writing the new `StateEntry` into `contract_state_changes`, mirroring the pattern already used in `deploy_contract.cairo` for its own preconditions.

### Proof of Concept
Not independently executable from the index alone (the divergence manifests only when comparing blockifier's actual transaction execution result against the Starknet OS's independent Cairo re-execution trace for the same transaction, which requires running the OS test harness). Conceptually:
1. Deploy/declare a Cairo1 contract exposing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)`.
2. Submit an `INVOKE` transaction calling `test_replace_class` with a class hash that has never been declared.
3. Blockifier rejects the syscall (`"is not declared"` error, as asserted in `deprecated_syscalls_test.rs:391`), causing the call/transaction to revert in the block actually produced by the sequencer.
4. Independently run the Starknet OS Cairo program over the same transaction data (as done in `starknet_os_flow_tests`); because `execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo` has no declared-class assertion, it will accept the same undeclared class hash and write it into `contract_state_changes`, producing a state diff inconsistent with the one blockifier actually committed for that block.

I was not able to fully verify from the index alone whether some other guard (e.g., an `is_reverted` check derived from the actual blockifier execution outcome, gating whether the OS even enters this code path) fully neutralizes the divergence in all cases — this depends on execution-flow details in `select_execute_entry_point_func` / revert-log handling that I could not fully trace within the available index. A Devin session with full repository access and the ability to run the `starknet_os_flow_tests` / OS test harness would be needed to conclusively confirm whether the divergence surfaces as an actual proof/state-root mismatch or is caught elsewhere.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-60)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L513-516)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
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
