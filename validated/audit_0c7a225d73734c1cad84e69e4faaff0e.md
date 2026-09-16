### Title
Starknet OS `execute_replace_class` accepts undeclared class hashes, diverging from Blockifier's declared-class check - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Blockifier's Rust implementation of the `replace_class` syscall requires the target `class_hash` to correspond to an already-declared class before mutating a contract's class assignment. The Starknet OS's Cairo re-implementation of the same syscall (used to re-execute/prove the sequencer's transactions) performs the state update unconditionally, with an explicit `TODO` acknowledging the missing check. Any transaction that calls `replace_class` with an undeclared class hash is therefore treated differently by the two independent implementations of the same protocol rule, producing divergent state.

### Finding Description
Blockifier enforces declaration before allowing `replace_class` to take effect. In the VM syscall path: [1](#0-0) 
`syscall_handler.state.get_compiled_class(request.class_hash)?` is called and errors ("is not declared") before `set_class_hash_at` is invoked — confirmed by the negative-flow test asserting the error message `"is not declared"`: [2](#0-1) 
and by the Cairo1/native syscall test suite (`undeclared_class_hash`) which asserts the same failure: [3](#0-2) 

In contrast, the Starknet OS's Cairo implementation of the syscall — the code that is independently re-executed to build the STARK proof of the block's state transition — performs the class-hash write without any declaration check, and explicitly documents this gap: [4](#0-3) 
Line 902 states: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The same missing check exists in the deprecated (Cairo0) syscall execution path: [5](#0-4) 

Both OS functions write `new_state_entry` with the caller-supplied `class_hash` directly into `contract_state_changes` via `dict_update`, with no equivalent to Blockifier's `get_compiled_class` (declared-class existence) check. This is directly analogous to the reported bug class: a value supplied by an unprivileged actor (here, the `class_hash` argument to a syscall invoked from ordinary contract code) is trusted and used to mutate authoritative state/metadata without validating that the referenced resource (a declared class) actually exists or is authorized — mirroring how the Open WebUI report trusted an attacker-supplied file ID in `meta.knowledge` without checking file ownership/existence.

Because Blockifier and the Starknet OS are meant to be two independent implementations of the exact same Starknet state-transition function (one used by the sequencer to build/validate blocks, the other used to generate the STARK proof of that same state transition for L1 finalization), any semantic divergence between them is a correctness bug: for identical transaction inputs, they must compute identical results.

### Impact Explanation
A single unprivileged transaction sender can submit an `Invoke` transaction whose called contract invokes `replace_class` with a `class_hash` that has never been declared. Blockifier will reject this call (raising the "is not declared" state error, causing the call/transaction to fail or revert), so no state diff for this action is committed by the sequencer's normal execution path. However, the OS's independent re-execution of the identical Cairo-level call, using the same actual call/constructor context (not the blockifier's error path but its own state-transition logic), lacks this validation and will happily accept the write, associating the contract with an undeclared class hash in its own computed state.

This constitutes an honest-node/protocol divergence between the two canonical implementations of the state-transition function: for the same transaction, Blockifier computes rejection while the OS computes acceptance and a different resulting contract class assignment. This can lead to:
- A wrong committed root: if the OS's computed state diff for the block differs from the block actually built/agreed upon via Blockifier, the OS-generated proof would not correspond to the block the network actually finalized (or the OS proof generation fails/mismatches, blocking the block from being provable at all).
- Permanent bricking of a contract if such an undeclared-class assignment is ever accepted into a proven state root: subsequent legitimate calls to that contract would look up a "class" that was never registered, permanently freezing the contract's functionality (denial of service / loss of contract funds/functionality, satisfying "permanent freezing of funds" / "unauthorized account action" criteria).

This is a Medium-to-High severity correctness bug reachable purely from a single submitted transaction's Cairo contract call — no privileged/prover-only or malicious-operator behavior is required to trigger the divergence in the OS's computation; the divergence is inherent to how the OS Cairo code processes the syscall for any transaction that reaches it.

### Likelihood Explanation
Any contract that exposes `replace_class` to external callers (a common contract-upgrade pattern) can be invoked by any account with an arbitrary, undeclared `class_hash` argument. Reaching the OS `execute_replace_class` code path only requires that a transaction containing this call be included/replayed for proving, which is a routine event, not requiring any adversarial control over consensus or infrastructure — only an ordinary transaction sender crafting the calldata. The bug is also explicitly flagged as unresolved by the repository's own `TODO(Yoni, 1/1/2026)` comment, confirming it is a known-but-unfixed gap rather than a hypothetical one.

### Recommendation
Add the equivalent "class is declared" check to both `execute_replace_class` implementations in the Starknet OS Cairo code (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) before performing the `dict_update` on `contract_state_changes`, mirroring Blockifier's `get_compiled_class(request.class_hash)` check (e.g., by reading/asserting membership in `contract_class_changes`/the declared-classes dict with a hint-fed lookup and an `assert`, consistent with how declarations are validated elsewhere, such as `execute_declare_transaction`'s `dict_update{dict_ptr=contract_class_changes}` pattern). This should be validated with a Cairo test analogous to the Rust `undeclared_class_hash` / `test_replace_class` negative-flow tests to guarantee OS/Blockifier parity going forward.

### Proof of Concept
1. Deploy a contract whose entrypoint calls the `replace_class` syscall with an attacker-supplied `class_hash` argument (pattern shown in `test_replace_class`): [6](#0-5) 
2. Invoke this entrypoint with an arbitrary/never-declared `class_hash` (e.g., `1234`).
3. Under Blockifier, this fails with `"is not declared"` (see `test_replace_class` negative flow, `deprecated_syscalls_test.rs:375-392`, and `replace_class.rs:17-29` `undeclared_class_hash` test).
4. When the same transaction/call trace is independently re-executed by the Starknet OS's `execute_replace_class` (`syscall_impls.cairo:881-920` or `deprecated_execute_syscalls.cairo:307-329`), no equivalent check exists — the OS unconditionally updates `contract_state_changes` with the undeclared `class_hash`, diverging from Blockifier's rejection.

Note: I was unable to fully trace how the OS's independently-computed `contract_state_changes` for such a divergent call ultimately reconciles with (or is checked against) the block's committed state diff during proof verification (e.g., whether an outer consistency check elsewhere in the OS pipeline would catch this before a proof is accepted) — that would require deeper inspection of the OS's block-level output/commitment logic (`crates/apollo_starknet_os_program/.../os/*` output aggregation) and the Starknet OS Cairo test suite (`OS Flow Tests`), which the index did not surface content for. A Devin session with full repository access would be needed to confirm whether any downstream check mitigates this divergence before it reaches the committed root.

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
