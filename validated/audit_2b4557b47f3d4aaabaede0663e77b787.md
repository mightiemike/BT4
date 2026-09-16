## Finding: `replace_class` syscall validation is missing in the Starknet OS re-execution path, while blockifier enforces it

### Title
Missing declared-class / version validation in Starknet OS `execute_replace_class` diverges from blockifier's enforced checks - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The sequencer's execution engine (blockifier) enforces two invariants when a contract calls the `replace_class` syscall: (1) the target class hash must already be declared, and (2) a Cairo1 (V1) class cannot be replaced by a Cairo0 (V0) class hash. Both checks are proven by the syscall unit tests. However, the Starknet OS's own Cairo implementation of the same syscall, which is used to independently re-execute transactions and prove the resulting state transition, performs neither check.

### Finding Description
In blockifier, `replace_class` is validated before mutating state: the deprecated syscall handler explicitly reads the class to ensure it is declared before calling `set_class_hash_at`: [1](#0-0) 

The dedicated test suite for the current (non-deprecated) syscall confirms blockifier additionally rejects undeclared class hashes with `"is not declared"` and rejects downgrading a V1 class to a V0 class hash with `"Cannot replace V1 class hash with V0 class hash"`, for both the CASM VM and Cairo Native runners: [2](#0-1) 

In contrast, the Starknet OS Cairo program that re-executes transactions (used for proving / Starknet OS re-execution as referenced in the analog scope) contains an explicit TODO acknowledging the check is not implemented, and unconditionally overwrites the contract's class hash in `contract_state_changes`: [3](#0-2) 

The same unconditional, unchecked class-hash overwrite exists in the deprecated-syscall Cairo implementation used by the OS: [4](#0-3) 

On the Rust side, the OS's syscall executor for `replace_class` is a pure no-op that performs no state read or validation at all — it simply returns `Ok(ReplaceClassResponse {})`, leaving all state mutation to the Cairo hint-driven code shown above: [5](#0-4) 

Because the OS's `execute_replace_class` never checks that a compiled class exists for the given hash (unlike blockifier's `get_compiled_class` check) and never checks V0/V1 compatibility, the OS's model of "the transaction execution" for this syscall is strictly more permissive than blockifier's. Any invocation where blockifier would legitimately fail/revert `replace_class` (undeclared class hash, or V1→V0 downgrade) would instead be silently accepted by the OS's Cairo re-execution logic if it were driven by a hint that supplies the class hash without accompanying declaration validation.

### Impact Explanation
If the Starknet OS's re-execution/validation of a block's syscalls does not enforce the same constraints as blockifier, then a maliciously crafted (or buggy) execution trace could cause the OS to accept a `replace_class` state transition that blockifier itself would never have produced. This directly threatens the "wrong committed root" and "honest-node divergence" categories from the validation rules: the block's committed state root (computed via OS re-execution and used in the STARK proof / state commitment) can diverge from what an honestly-run sequencer's blockifier execution would produce, or the OS could "validate" a state transition that never should have been possible (setting a contract's class to a hash with no declared class, corrupting its ABI/CASM binding, or downgrading a Cairo1 contract to Cairo0 semantics unexpectedly) — a form of unauthorized account action reachable from a single contract call to `replace_class`.

### Likelihood Explanation
The `replace_class` syscall is directly reachable by any deployed contract executing normal Cairo code — no privileged role is required, matching the "unprivileged transaction sender ... syscalls" reachability requirement. The divergence is deterministic and always present in the current code (marked by an explicit unresolved `TODO`), not a rare race condition.

### Recommendation
Add the same validation to the OS's `execute_replace_class` implementations (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that blockifier performs: verify a compiled class exists for `class_hash` (via the appropriate hint reading committed class data) and enforce the Cairo0/Cairo1 compatibility rule, before updating `contract_state_changes`. Resolve the `TODO(Yoni, 1/1/2026)` comment as part of this fix and add OS-level tests mirroring `replace_class.rs`'s `undeclared_class_hash` and `cairo0_class_hash` cases to guarantee re-execution parity with blockifier.

### Proof of Concept
A concrete end-to-end PoC (crafting a divergent OS execution trace and demonstrating a differing committed root vs. blockifier) requires access to the OS hint-generation/proving harness to confirm whether the discrepancy is actually reachable in the production proving pipeline (i.e., whether hints ever allow supplying an undeclared class hash to `execute_replace_class`) or whether earlier state-diff construction from blockifier itself constrains the hint inputs so this path is unreachable in practice. This could not be fully verified within the available tooling/iterations; a Devin session with full repository and test-execution access would be needed to trace hint construction (`GetContractAddressStateEntry`) and confirm whether malicious/divergent hints can actually be fed to this Cairo function during proving, which would allow constructing a full reproducible PoC.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L17-53)
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

**File:** crates/starknet_os/src/hint_processor/snos_syscall_executor.rs (L319-326)
```rust
    fn replace_class(
        _request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        _syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        Ok(ReplaceClassResponse {})
    }
```
