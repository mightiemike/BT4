This is a promising lead. Let me verify the discrepancy between the blockifier's `replace_class` syscall handling (which validates the class is declared) and the Starknet OS Cairo implementation (which appears to skip this check per the TODO comment).### Title
Starknet OS `execute_replace_class` omits the declared-class check that Blockifier enforces, allowing honest-node divergence and unprovable/incorrectly-provable state transitions - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
This is the analog of the reported "Burner leaves unsafe approval to the previous MINTR" bug class: a reference to another authority (MINTR contract / declared class) is trusted and acted upon without re-validating it at the point of use. In the sequencer, the Starknet OS's Cairo implementation of the `replace_class` syscall updates a contract's `class_hash` in `contract_state_changes` **without verifying that the target class hash is actually declared**, while the Rust `blockifier` execution path (the code that actually builds/validates blocks) enforces this check. This creates a divergence between the state transition Blockifier will accept/reject and the state transition the OS will accept/reject during re-execution/proving.

### Finding Description
In `execute_replace_class` in `syscall_impls.cairo`, the class hash from the syscall request is written directly into the contract's state entry, with an explicit TODO stating the missing check: [1](#0-0) 

The same omission exists in the deprecated (Cairo0) syscall path, `execute_replace_class` in `deprecated_execute_syscalls.cairo`, which likewise writes the new class hash into `contract_state_changes` with no declaration check at all: [2](#0-1) 

By contrast, Blockifier's Rust implementation of the same syscall explicitly reads the compiled class for `request.class_hash` before allowing the update — which fails with `UndeclaredClassHash` if the class was never declared: [3](#0-2) 

and Blockifier's syscall tests confirm this negative-flow behavior is a hard requirement of the production execution engine: [4](#0-3) [5](#0-4) 

So today: Blockifier (the code path used to actually build and validate blocks in the sequencer) rejects `replace_class_syscall(class_hash)` calls when `class_hash` is not declared, but the Starknet OS Cairo program (used for re-execution / proof generation, listed explicitly as in-scope) does not perform this check and will happily accept the same call, updating `contract_state_changes` for the contract address to an undeclared/arbitrary class hash.

### Impact Explanation
This is a Blockifier vs. Starknet-OS consistency bug, directly matching the "Starknet OS re-execution" and "state reads" categories in scope:
- If a transaction that calls `replace_class_syscall` with an undeclared class hash is ever fed to the OS for re-execution (e.g. during proving or OS-based re-execution/verification flows), the OS will silently accept and commit a state transition that the real sequencer (Blockifier) would have rejected as invalid (revert / execution failure). This is a **wrong committed root** / **honest-node divergence** risk: two different implementations of the state machine (Blockifier vs. OS) reach different verdicts on the validity of the same transaction, meaning the OS could prove a block containing a class-hash update that never happened in Blockifier's execution, or fail to match Blockifier's actual state diff.
- Because the assigned class hash need not correspond to any content that was ever declared (paid for, verified, or hashed against real Sierra/CASM), a contract's class pointer can end up pointing to a hash with no backing compiled class in the OS's model of state, undermining the integrity of the state root and CASM/Sierra correspondence relied upon by proving and later execution.

### Likelihood Explanation
`replace_class_syscall` is directly callable by any account/contract with no special privilege — it's a standard Cairo syscall reachable from any unprivileged transaction sender. The Blockifier-side guard exists specifically because this input is attacker-controlled, confirming the intended invariant. The equivalent guard is simply missing on the OS Cairo side (evidenced by the explicit `TODO` comment), making the divergence trivially triggerable by any user submitting a transaction from an account contract that calls `replace_class_syscall` with an undeclared class hash.

### Recommendation
Add the same declared-class validation to the Starknet OS Cairo implementations of `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that Blockifier already performs — i.e., before updating `contract_state_changes`, verify (via a lookup analogous to `get_compiled_class`/declared-class dict) that `class_hash` corresponds to a declared class, and fail/revert the syscall (matching Blockifier's `UndeclaredClassHash` error semantics) if it does not. Additionally, mirror Blockifier's V1-to-V0 downgrade restriction ("Cannot replace V1 class hash with V0 class hash") if that check is likewise absent from the OS path, to fully align the two execution engines.

### Proof of Concept
1. Deploy an account/test contract exposing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)` (this contract already exists in the test fixtures, e.g. `cairo_steps_test_contract.cairo`): [6](#0-5) 
2. Submit a transaction invoking `test_replace_class` with an arbitrary, never-declared `class_hash` (e.g. `felt!(1234_u16)`, matching the negative-flow test used against Blockifier).
3. In Blockifier's normal execution path, this transaction reverts/fails with `UndeclaredClassHash` (as verified by the existing test at `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`).
4. Feed the same transaction/trace to the Starknet OS Cairo program's `execute_replace_class` (`syscall_impls.cairo:881-920` or the deprecated equivalent). Because the declared-class check is not implemented there (per the explicit TODO on line 902), the OS accepts the syscall and updates `contract_state_changes` for the target contract to the undeclared class hash — producing a state transition inconsistent with what Blockifier would have committed for the same input.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-914)
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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
