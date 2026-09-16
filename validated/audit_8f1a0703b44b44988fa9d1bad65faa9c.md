## Finding

### Title
Starknet OS `execute_replace_class` syscall handler omits the "class is declared" check enforced by Blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS Cairo re-implementation of the `REPLACE_CLASS` syscall does not verify that the target `class_hash` corresponds to an actually declared class before writing it into the contract's `StateEntry`, while the Rust Blockifier — the component that performs the "real" (block-building) execution of the exact same syscall — does enforce this check and additionally restricts replacement to Cairo1 classes.

### Finding Description
In `execute_replace_class` (the "new syscalls" implementation used by the Starknet OS), the class hash taken straight from the syscall request is written into the contract's state entry with no validation: [1](#0-0) 

The comment on line 902 explicitly documents the gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The same omission exists in the deprecated syscalls implementation: [2](#0-1) 

By contrast, the Rust Blockifier — which executes the same `replace_class` syscall when building/validating a block — explicitly ensures the class is declared (`get_compiled_class`) and additionally rejects replacement with a Cairo0 (deprecated) class: [3](#0-2) 

and, for the deprecated syscall path: [4](#0-3) 

Blockifier's own tests confirm this is a deliberate, security-relevant check (`"is not declared"`, `"Cannot replace V1 class hash with V0 class hash"`): [5](#0-4) 

This is structurally the same bug class as the Discourse issue: one code path (Blockifier) performs the authorization/validity check on the object being acted upon, while a second, related code path that performs the equivalent operation over the same protocol object (the Starknet OS's Cairo re-execution of the identical `REPLACE_CLASS` syscall, used to generate the STARK proof of block validity) fails to re-apply that same check.

### Impact Explanation
The Starknet OS is the component whose execution trace is what actually gets proven and anchored on L1 (via the state/commitment computation) — it is the final arbiter of "was this state transition valid." If the OS's internal notion of validity for `REPLACE_CLASS` is weaker than Blockifier's (i.e., it accepts any `class_hash`, declared or not, and even Cairo0 downgrades that Blockifier explicitly forbids), any place where the OS's re-executed state diverges from what Blockifier computed can result in a class hash written to committed state that does not correspond to a declared, executable class. Contract code that ends up "replaced" to a hash with no matching compiled class breaks subsequent calls to that address for every honest full node (they will fail to load the class), which is a state root correctness / honest-node divergence hazard, and undermines the guarantee that the OS re-verification catches any divergence from Blockifier's semantics.

### Likelihood Explanation
The check is missing unconditionally in both the current and deprecated `REPLACE_CLASS` syscall handlers of the OS Cairo program — it is not gated behind any rare condition, only masked in the honest, single-implementation flow because Blockifier (the block-building engine) currently happens to enforce the rule first. Any scenario where the OS's syscall execution is authoritative or where its hinted state can diverge from Blockifier's cache (e.g. through the `GetContractAddressStateEntry`/state hints it trusts) reaches the unguarded write immediately, with no additional preconditions.

### Recommendation
Add the same validation the Blockifier applies — that the class hash is declared (present in `contract_class_changes`/committed class table) and that it is not a Cairo0 class being substituted in for a Cairo1 class — to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class`, and add regression tests that force the OS execution to reject an undeclared/wrong-version class hash the same way Blockifier does.

### Proof of Concept
1. Deploy a contract that invokes `replace_class_syscall(class_hash)` with an arbitrary, never-declared `class_hash` (as done by `test_replace_class` in `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo`).
2. Under Blockifier execution this correctly reverts with `"is not declared"` (see `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`).
3. Trace the equivalent operation through `execute_replace_class` in `syscall_impls.cairo`: the function reads `request.class_hash`, updates the `StateEntry` and `contract_state_changes` dict, and returns success — with no lookup against declared classes at all, confirming the OS-side implementation would accept the operation that Blockifier rejects.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
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
