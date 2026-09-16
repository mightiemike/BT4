### Title
`replace_class` syscall in the Starknet OS omits the "class must be declared" check present in the blockifier, allowing state divergence between execution and re-execution - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
This is an analog of the DODO "fake-token" bug class: DODO's `init()`/`sync()` flow let an attacker feed unvalidated state (a fake token address) into a sensitive function that should have first verified the input was legitimate. Here, an unprivileged contract can call `replace_class_syscall` and have the Starknet OS accept an **undeclared** class hash for a deployed contract's class pointer, whereas the same operation is rejected by the blockifier at block-production time. This creates a validation gap between the two "identical" execution engines that the protocol depends on for consensus (blockifier at sequencing time, Starknet OS/prover at re-execution time).

### Finding Description
The blockifier's `replace_class` syscall handler explicitly verifies the class is declared before allowing it to be set on a contract's storage entry: [1](#0-0) 
This is also confirmed by the corresponding negative test, which requires the error `"is not declared"` when the class hash is undeclared: [2](#0-1) 

However, the Starknet OS (Cairo, used for re-execution / proving via the Starknet OS) implements `execute_replace_class` in two places, and **neither performs this declared-class check**. The current (non-deprecated) syscall path has an explicit `TODO` acknowledging the missing check: [3](#0-2) 
The deprecated syscall path (used by Cairo0 contracts) has the identical gap, with no check at all: [4](#0-3) 

In both OS implementations, the syscall simply overwrites the `StateEntry.class_hash` field of the calling contract via `dict_update` on `contract_state_changes`, with `class_hash` taken directly from syscall input, with no lookup into `contract_class_changes` or any declared-classes structure to confirm the hash corresponds to an actually-declared class. This mirrors DODO's bug where `init()`/`sync()` accepted attacker-supplied token addresses/balances without validating their legitimacy before they were trusted downstream.

### Impact Explanation
This is a divergence between the blockifier (used for real sequencing and state commitment during block building) and the Starknet OS (used to prove/re-execute the block for the STARK proof and, in this repo, apparently also intended to reject the same class of transaction). If a sequencer, prover, or the OS-driven flow accepts a `replace_class` call with an undeclared class hash where the blockifier would reject it as an execution error, the two systems disagree on whether that transaction reverts. This is exactly the class of "honest-node divergence" / "wrong committed root" issue called out as in-scope: a contract's `class_hash` field in the committed state could end up holding a value that has no corresponding declared compiled class, corrupting the contract-class Merkle tree commitment or producing state that cannot be executed consistently by all parties (e.g., subsequent calls to that contract would look up compiled class data that doesn't exist). Depending on how strictly the flow test harness / OS output stage cross-checks the class tree, this could lead to a committed state root that is inconsistent with actual declared classes, i.e. a state divergence bug directly reachable by any account issuing an ordinary `replace_class_syscall` invoke transaction.

### Likelihood Explanation
The `replace_class` syscall is callable by any contract via ordinary `invoke` transactions - it requires no special privileges, only that the calling contract account exists and executes `replace_class_syscall(class_hash)` with an arbitrary, unvalidated `class_hash` felt. The bug is directly acknowledged by an in-repo `TODO` comment ("Check that there is a declared contract class with the given hash"), confirming the gap is a known, currently-unaddressed omission rather than a stylistic difference. The blockifier's matching check (and its explicit negative test) demonstrates that this validation is considered a necessary invariant in the execution engine, making its absence in the OS's parallel implementation a genuine cross-component inconsistency.

### Recommendation
Add the same "class is declared" check to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::replace_class` — i.e., look up `class_hash` in `contract_class_changes` (or the equivalent declared-classes structure available to the OS) and fail/revert the syscall if it is not present, before performing the `dict_update` on `contract_state_changes`. Resolve the outstanding `TODO(Yoni, 1/1/2026)` as part of this fix, and add a Cairo-level test analogous to the Rust `replace_class.rs::undeclared_class_hash` test to lock in the behavior.

### Proof of Concept
1. Deploy a contract implementing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)` (as in `blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo`).
2. As any unprivileged account, submit an invoke transaction calling `test_replace_class` with an arbitrary/never-declared `class_hash` felt (e.g., `1234`).
3. In blockifier execution, this call is rejected with "... is not declared" (as verified by `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs::undeclared_class_hash`).
4. In the Starknet OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), the same call proceeds unconditionally, updating `contract_state_changes` to set the contract's `class_hash` to the undeclared value with no error - demonstrating the missing validation and the resulting execution/re-execution divergence.

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
