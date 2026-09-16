### Title
Starknet OS `execute_replace_class` accepts undeclared class hashes, diverging from blockifier's declared-class validation - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall lets any contract change its own class hash. The sequencer's real execution engine (blockifier) validates that the new class hash is a *declared* class before applying the change, and reverts otherwise. The Starknet OS's Cairo re-implementation of the same syscall, used to independently recompute state during proving, performs no such check — an omission the code itself flags with an open TODO. This creates a case where the sequencer and the OS can compute different final states for the same block, i.e., a consensus-critical execution divergence, directly analogous to the reported bug class of "accepting caller-controlled parameters without the validation an honest implementation is supposed to enforce."

### Finding Description
Any unprivileged contract can invoke `replace_class_syscall(class_hash)` from `__execute__` with an arbitrary, attacker-chosen `class_hash`, including one that was never declared.

In blockifier (the execution engine that actually produces the block and is run by every sequencer/full node), this call is explicitly gated: [1](#0-0) 
The comment "Ensure the class is declared (by reading it)" and the call to `get_compiled_class` before `set_class_hash_at` mean an undeclared class hash causes the syscall (and the transaction/call) to fail with `StateError::UndeclaredClassHash`, as directly verified by tests: [2](#0-1) [3](#0-2) 

However, the Starknet OS's Cairo implementation of the identical syscall — used during proof generation to independently re-derive `contract_state_changes` and ultimately the committed state root/block hash — has no such check: [4](#0-3) 
The code directly writes `new_state_entry` with the caller-supplied `class_hash` into `contract_state_changes` with only a comment acknowledging the gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`

The same omission exists (without even a TODO) in the deprecated syscall path used for Cairo0 contracts: [5](#0-4) 

Because the OS's Cairo logic for syscall semantics (as opposed to non-deterministic dictionary lookups fed via hints) is executed independently in Cairo rather than blindly trusting blockifier's Rust-side revert decision, a transaction that blockifier reverts due to `UndeclaredClassHash` can instead be accepted by the OS's replay, applying a class-hash state change that never actually happened on the real chain.

### Impact Explanation
This is a wrong-committed-root / honest-node-divergence class bug: the block/state actually produced by the sequencer (via blockifier, enforcing the declared-class invariant) can differ from the state the Starknet OS computes when re-executing the same block to build the proof and state commitment. Since the OS's output feeds the Patricia-tree state commitment and ultimately the block hash / L1-verified state root, this divergence can cause the network to attempt to commit an incorrect root, break proof verification against the real chain state, or allow a class-hash state entry (pointing at an undeclared/nonexistent class) to be recorded in the committed state — undermining state integrity and consensus between execution and proving. This satisfies the "wrong committed root or block hash / honest-node divergence" impact bar.

### Likelihood Explanation
The trigger requires nothing more than an ordinary invoke transaction from any account calling a contract that executes `replace_class_syscall` with an undeclared class hash — no special privileges, no L1 message, and no cooperation from a validator/operator are required. The only reason this hasn't obviously caused visible failures is presumably that in-practice test/CI traffic doesn't exercise this specific negative path against the OS, and the gap is explicitly still open per the `TODO(Yoni, 1/1/2026)` marker in the source.

### Recommendation
Add the missing declared-class check to the Starknet OS's `execute_replace_class` (in `syscall_impls.cairo`) and to the deprecated variant in `deprecated_execute_syscalls.cairo`, mirroring blockifier's `get_compiled_class`/`UndeclaredClassHash` check, so that both execution paths agree bit-for-bit on whether a `replace_class` syscall succeeds or reverts for a given class hash. Add OS-side flow tests analogous to `deprecated_syscalls_test.rs::test_replace_class` and `syscall_tests/replace_class.rs::undeclared_class_hash` that assert the OS rejects/reverts replace_class calls with undeclared class hashes identically to blockifier.

### Proof of Concept
1. Deploy any contract exposing an entry point that calls `replace_class_syscall(class_hash)` (e.g. the existing `test_replace_class` function in the test contracts, reachable via ordinary invoke transaction): [6](#0-5) 
2. Submit an invoke transaction calling `test_replace_class` with a `class_hash` that has never been declared on-chain.
3. Observe blockifier reverts the call with `"is not declared"` (as reproduced by the existing unit test `undeclared_class_hash`): [7](#0-6) 
4. When the same block/transaction is re-executed by the Starknet OS for proving (`execute_replace_class` in `syscall_impls.cairo`), no equivalent declared-class check exists, so the state entry's `class_hash` field is unconditionally overwritten to the undeclared value in `contract_state_changes`, producing a state diff that blockifier never actually committed — demonstrating the execution/proving divergence.

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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L513-516)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
