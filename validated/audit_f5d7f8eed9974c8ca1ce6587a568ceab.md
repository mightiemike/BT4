### Title
Starknet OS `execute_replace_class` Skips Declared-Class Verification, Diverging From Blockifier Execution - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Cairo1 `replace_class` syscall handler in the Starknet OS re-execution program blindly writes the requested `class_hash` into the contract's state entry without verifying that a class with that hash is actually declared. This is analogous to the reported bug class: input that should be validated against an expected "type"/existence invariant is accepted implicitly and only fails (or silently diverges) downstream, rather than being rejected where it is first consumed. The Rust `blockifier` — which is the component that actually decides transaction success/failure and produces the block's state diff — enforces this invariant, but the Starknet OS Cairo implementation that re-executes the same transaction for proof generation does not.

### Finding Description
When a contract calls the `replace_class` syscall, `blockifier`'s handlers explicitly check that the target class hash is declared before mutating state: [1](#0-0) 

and negative-flow tests confirm an undeclared class hash causes the transaction to fail with "is not declared": [2](#0-1) [3](#0-2) 

In contrast, the Starknet OS's own Cairo implementation of the same syscall (used to independently re-execute transactions when generating the OS trace/proof) performs no such check — it contains an explicit acknowledged gap: [4](#0-3) 

The comment at line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms the OS unconditionally reads the current `StateEntry` via the `GetContractAddressStateEntry` hint and overwrites `class_hash` with the caller-supplied, unverified value — with no `is_declared`/`get_compiled_class` check analogous to blockifier's. The deprecated (Cairo0) OS syscall path has the identical gap: [5](#0-4) 

This mirrors the report's root cause: a component that should validate an identifier/type before trusting it (declared-class existence, in this analog; ERC20-vs-NFT identity, in the original) instead defers validation to a downstream consumer, and the two "consumers" (blockifier vs. Starknet OS) apply different rules to the same untrusted input.

### Impact Explanation
The Starknet OS is the program whose execution the STARK proof attests to, and its resulting state changes (via `contract_state_changes`) feed the Patricia-tree/state commitment pipeline that ultimately determines the committed state root and block hash. If the OS accepts and commits a `replace_class` to an undeclared (or otherwise invalid) class hash where the actual sequencer/blockifier execution would have reverted the transaction, the state diff computed by OS re-execution can diverge from the one produced by the real transaction execution. Divergence between the OS-committed state root and the blockifier-derived state root is exactly the "wrong committed root or block hash / honest-node divergence" impact category called out as in-scope, and can lead to proof/verification failures or, more severely, permanent inconsistency between what nodes believe is canonical state and what the proof commits to — undermining state finality guarantees for the entire network, not just a single account.

### Likelihood Explanation
Any unprivileged contract can trigger `replace_class` with an arbitrary `class_hash` via a normal invoke transaction — no special privileges are required, matching the "unprivileged transaction sender / contract call" reachability requirement. The only reason this hasn't manifested as an observed incident is presumably that in practice the OS's hint program is fed a class hash consistent with a hash that already passed blockifier's check in the live sequencer flow; the missing Cairo-level assertion is nonetheless a latent verification gap explicitly flagged by the maintainers' own TODO, and any code path or replay flow where OS re-execution is decoupled from blockifier's prior validation (e.g., independent replay/testing pipelines, or future refactors relying on the OS check being self-sufficient) is exposed.

### Recommendation
Add an explicit "is class hash declared" check in `execute_replace_class` (both `syscall_impls.cairo` and the deprecated `deprecated_execute_syscalls.cairo`) mirroring the blockifier's `state.get_compiled_class(request.class_hash)?` check, asserting failure (and following the standard `write_failure_response`/revert pattern) when the class is not declared, so that the OS enforces the identical invariant as the sequencer's execution engine.

### Proof of Concept
1. An attacker deploys a trivial contract exposing a `test_replace_class(class_hash)` entry point that calls the `replace_class` syscall (as in the existing test contract). [6](#0-5) 
2. The attacker invokes it with an undeclared/arbitrary `class_hash` value (e.g., `1234`).
3. In blockifier, `state.get_compiled_class(request.class_hash)` fails, producing the `"is not declared"` error and reverting the transaction — confirmed by the existing negative-flow test. [3](#0-2) 
4. When the same call trace is independently re-executed by the Starknet OS Cairo program (`execute_replace_class` in `syscall_impls.cairo`), no equivalent declared-class check exists — the state entry is unconditionally updated with the attacker-supplied class hash, per the code at lines 900-914, producing a different state transition than the one blockifier actually committed.

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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo0/test_contract.cairo (L319-323)
```text
@external
func test_replace_class{syscall_ptr: felt*}(class_hash: felt) -> () {
    replace_class(class_hash=class_hash);
    return ();
}
```
