### Title
Starknet OS `execute_replace_class` omits the declared-class check enforced by the Blockifier, allowing a contract to be bound to an undeclared class hash in the proven state transition - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall lets a contract change the class hash it points to. In the native (Rust) Blockifier execution path, this operation is explicitly gated on the target class being declared before the state entry is updated. The Starknet OS Cairo implementation of the same syscall — the code that is actually STARK-proved and therefore defines the authoritative "valid state transition" semantics — has this check missing and is marked with an open TODO, meaning a contract's class hash can be rebound to any value the syscall requests without verifying that a corresponding declared class exists. This mirrors the reported Keycloak bug class: an entity (a contract's class binding) is linked to another entity (a declared class) without validating the required precondition/permission on that second entity.

### Finding Description
The Blockifier's `replace_class` deprecated-syscall handler enforces the invariant "the class must be declared" before rebinding a contract's class hash: [1](#0-0) 

This is exercised and confirmed by tests that assert an undeclared class hash is rejected with an explicit `"is not declared"` error, and that a V0/V1 mismatch is also rejected: [2](#0-1) 

In contrast, the Starknet OS's Cairo implementation of the exact same syscall — `execute_replace_class` in `syscall_impls.cairo` — updates the contract's `StateEntry.class_hash` directly from the syscall request without any check that the class was ever declared. This is called out by an explicit TODO in the code itself: [3](#0-2) 

The same gap exists in the deprecated syscalls execution path of the OS: [4](#0-3) 

Because the Starknet OS program is the component that is re-executed and STARK-proved to attest to the correctness of a block's state transition, the constraints it enforces are the actual protocol rules being cryptographically guaranteed — the Blockifier's check is only an "off-chain" convenience/optimization for normal sequencer block-building, not a check the proof itself relies on. Any code path that reaches the OS's `execute_replace_class` without first passing through the Blockifier's stricter declared-class check (e.g., an alternate/duplicate implementation, a future change that removes/weakens the Blockifier-side check, or execution paths that build syscall traces independently for OS re-execution/proving) will have its output accepted by the OS/proof even though it violates the "class must be declared" invariant that the rest of the system assumes holds.

### Impact Explanation
If the declared-class invariant is not actually enforced within the proven state-transition function, then the guarantee "a contract's class hash always refers to a declared class" is not a property that the STARK proof establishes — it is merely an artifact of the current Blockifier implementation happening to check it before block inclusion. This is a soundness gap in the specification of a state transition: the honest-vs-proved semantics of `replace_class` diverge from what full nodes/verifiers assume is enforced by the proof. A future divergence between the Blockifier's admission-time check and the OS's proof-time check (e.g., a regression, alternate compilation path, or any component that produces OS execution traces without funneling through the exact same declared-class gate) can result in a state root that is accepted as valid despite containing a contract pointing to an undeclared/nonexistent class — i.e., an unauthorized/invalid state binding baked into the committed root, which the "State commitment and Patricia trees" / "block hash and commitments" / "Starknet OS re-execution" layers would then treat as canonical.

### Likelihood Explanation
Today the only known path a submitted transaction has to reach `replace_class` execution is via the Blockifier, which does perform the declared-class check, so a straightforward single-transaction exploit against the current consolidated sequencer is not immediately demonstrated. However, the bug class matches the reported analog precisely: the check that should gate the state-binding action is implemented only in one execution path (the "identity provider"/Blockifier side) and is explicitly absent — with an open TODO acknowledging it — in the second, authoritative path (the "organization"/OS side) that the protocol's proofs rely on. Given the codebase already tracks this as an open item (`TODO(Yoni, ...)`), and the OS is the module that ultimately defines correctness for re-execution/proving, this is a real correctness gap rather than defense-in-depth duplication.

### Recommendation
Add the same declared-class existence check to the Starknet OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that the Blockifier already performs — i.e., read/verify `contract_class_changes` (or the equivalent declared-classes dictionary) for `class_hash` before updating the `StateEntry`, and reject the syscall (or produce a failure response) if the class was never declared. Resolve the tracked TODO rather than leaving proof-time enforcement solely dependent on the Blockifier's admission-time behavior.

### Proof of Concept
Not independently reproducible as an end-to-end exploit against the current consolidated sequencer, since the Blockifier's admission-time check currently prevents an attacker-controlled transaction invoking `replace_class` with an undeclared class hash from ever reaching block inclusion: [5](#0-4) 
The vulnerability is demonstrated structurally by comparing the two implementations of the identical syscall: the Blockifier's check exists at [6](#0-5) 
while the Starknet OS's version omits it, as shown by the TODO at [7](#0-6)

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-53)
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

#[cfg_attr(feature = "cairo_native", test_case(RunnableCairo1::Native; "Native"))]
#[test_case(RunnableCairo1::Casm; "VM")]
fn cairo0_class_hash(runnable_version: RunnableCairo1) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(runnable_version));
    let empty_contract_cairo0 = FeatureContract::Empty(CairoVersion::Cairo0);
    let mut state = test_state(
        &ChainInfo::create_for_testing(),
        BALANCE,
        &[(test_contract, 1), (empty_contract_cairo0, 0)],
    );

    // Replace with Cairo 0 class hash.
    let v0_class_hash = empty_contract_cairo0.get_class_hash();

    let entry_point_call = CallEntryPoint {
        calldata: calldata![v0_class_hash.0],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err();

    assert!(error.to_string().contains("Cannot replace V1 class hash with V0 class hash"));
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
