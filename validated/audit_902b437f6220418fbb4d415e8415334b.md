Confirmed: the Cairo1 syscall path in the Starknet OS explicitly skips the "class is declared" check via a `TODO` comment, while the Rust blockifier implementations (both `deprecated_syscalls/hint_processor.rs::replace_class` and `syscalls/syscall_base.rs::replace_class`) enforce it. This is a genuine ACL/validation-check omission analogous to the MISP CVE's missing-check pattern, reachable by any contract via `replace_class_syscall`.

### Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes re-execution divergence from Blockifier - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Cairo implementation of the `replace_class` syscall in the Starknet OS (`execute_replace_class`, used for Starknet OS re-execution/proving) unconditionally overwrites a contract's `class_hash` state entry without validating that the target `class_hash` corresponds to a declared class. The Rust Blockifier, which actually executes transactions when building blocks, enforces this check and rejects/reverts calls that attempt to replace a contract's class with an undeclared (or otherwise invalid) class hash.

### Finding Description
`execute_replace_class` in `syscall_impls.cairo` reads the requested `class_hash` and directly updates `contract_state_changes` for the calling contract, explicitly marked with: [1](#0-0) 

By contrast, the Blockifier's Cairo0 syscall handler performs the check before mutating state: [2](#0-1) 

and the shared Cairo1 `syscall_base::replace_class` implementation (invoked from both the CASM VM and native execution paths) enforces the same declared-class and version-compatibility constraints, as shown by the negative test cases that assert on `"is not declared"` and `"Cannot replace V1 class hash with V0 class hash"`: [3](#0-2) 

Because the deprecated Cairo0 OS path (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) has the identical omission: [4](#0-3) 

any contract, when re-executed by the OS, can call `replace_class_syscall` with an arbitrary/undeclared class hash and have it silently succeed and be committed to `contract_state_changes`, whereas the same call in the Blockifier (the code path that actually produces the sequencer's committed block) would fail with `"is not declared"` (Cairo1) causing the inner call to revert, or with a state error (Cairo0). Since the syscall's success/failure also determines contract-level control flow (a `Result::Err` vs `Result::Ok` branch inside the calling contract), this is not just a state-diff mismatch but a full execution-path divergence between the two implementations that are supposed to compute identical results for the same block.

### Impact Explanation
The Starknet OS is used to re-execute committed blocks and generate the STARK proof (SNOS) that attests the sequencer's state transition is correct, ultimately anchoring the L2 state root that gets verified against L1. If the OS's replay of a transaction takes a different control-flow branch (succeeds where the Blockifier would fail) and commits a different class hash than the Blockifier actually wrote to the canonical state, the OS-computed output (state diff / commitment) diverges from the sequencer's actual committed state. This produces an honest-node divergence: the same transaction yields two different results depending on which component executes it, undermining the soundness of the OS-based proof of correctness and potentially causing block/state-root verification to fail (network unable to confirm/finalize blocks) or, in the worst case, allowing an incorrect state root to be proven and accepted.

### Likelihood Explanation
Any account or contract can trigger this by simply invoking `replace_class_syscall` with a class hash that has never been declared (or, for the Cairo0 path, any felt value). No special privileges, staking, or operator/prover collusion is required — a single ordinary transaction from an unprivileged sender is sufficient to exercise the mismatched code path.

### Recommendation
Add the same "class must be declared" (and version-compatibility, mirroring the Cairo1 Blockifier check that forbids replacing a V1 class hash with a V0 class hash) validation to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` before the state entry is updated, so the OS's replace_class semantics exactly match the Blockifier's `replace_class` behavior (including causing the same revert/failure response when the class is undeclared).

### Proof of Concept
1. Declare (but do not deploy) nothing — deploy a Cairo1 contract exposing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)`.
2. Submit an invoke transaction from any account calling `test_replace_class` with an arbitrary, never-declared `class_hash` felt (e.g. `0x1234`).
3. In the Blockifier (actual block execution), this call fails with `"... is not declared ..."` as verified in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs::undeclared_class_hash`, causing the inner call to revert and no class-hash state change to be committed.
4. When the Starknet OS re-executes the same block for proving, `execute_replace_class` in `syscall_impls.cairo` performs no declared-class check and unconditionally writes the requested `class_hash` into `contract_state_changes`, producing a state diff that differs from the one actually committed by the sequencer.

### Citations

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
