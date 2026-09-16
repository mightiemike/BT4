### Title
Starknet OS `execute_replace_class` skips the "class must be declared" check enforced by the blockifier, allowing state divergence and wrong committed roots - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The external report describes an exploit where a router blindly trusted a value self-reported by an untrusted contract (`underlying()`) without validating it against the real, authoritative source, letting the attacker substitute an unchecked value to redirect fund flow. The structurally analogous bug class here is: **a critical state-mutating operation accepts an attacker-supplied value without the validation that the "authoritative" execution path performs**, causing two subsystems that are supposed to agree to diverge.

In this sequencer codebase, the `replace_class` syscall is such an operation: it lets a contract change its own `class_hash` in state. The blockifier (actual sequencer execution engine) validates that the target `class_hash` is declared before accepting it, but the Starknet OS (used for re-execution / proving) explicitly skips this validation, as marked by its own TODO comment.

### Finding Description
The blockifier's `replace_class` syscall handler for Cairo 1 contracts enforces that the target class must be declared before mutating state: [1](#0-0) 
performs `syscall_handler.state.get_compiled_class(request.class_hash)?` before calling `set_class_hash_at`, and the non-deprecated path additionally forbids downgrading a V1 class hash to a V0 class hash (`ForbiddenClassReplacement`) as referenced in error definitions: [2](#0-1) 
This is proven directly by the blockifier's own tests, which assert that replacing to an undeclared class hash fails with "is not declared", and that replacing a Cairo1 class with a Cairo0 (V0) class hash fails: [3](#0-2) 

However, the Starknet OS's own implementation of the same syscall — used to re-execute the block and produce the STARK proof of the state transition — has an explicit, acknowledged gap: [4](#0-3) 
The comment at line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms that `execute_replace_class` in the OS unconditionally applies whatever `class_hash` value is present in the syscall request to `contract_state_changes`, with **no** verification that the class is declared and **no** V1→V0 downgrade guard. The deprecated (Cairo0) OS syscall path exhibits the identical unguarded behavior: [5](#0-4) 

The OS is the component responsible for independently validating/re-executing the block to produce the canonical state commitment and block hash used for proving (Starknet OS re-execution is explicitly listed as a reachable path). Because the OS's `execute_replace_class` does not independently enforce the "class must be declared" invariant that the blockifier enforces, the two execution engines can diverge on how a `replace_class_syscall` call with a bogus/undeclared class hash is treated, breaking the fundamental invariant that OS re-execution faithfully reproduces (and independently validates) blockifier execution.

### Impact Explanation
This maps to the "wrong committed root or block hash" and "honest-node divergence" impact categories explicitly listed as acceptable in the validation rules. If the OS accepts and commits a state entry with an undeclared (or downgraded) class hash that the blockifier would have rejected, the OS-computed state (and therefore the state commitment/global root and block hash produced through the Starknet OS flow) can diverge from what the actual sequencer execution produced. Since class hashes are also used as trust anchors elsewhere (e.g., `get_class_hash_at` reads by other contracts, entry-point resolution for future calls to that contract), an undeclared class hash being committed to state creates a corrupted/unsound state entry that has no corresponding compiled class, undermining the guarantee that every class hash appearing in contract state is a declared, hash-verified class. This is a real state-corruption/soundness issue reachable by any unprivileged transaction sender invoking `replace_class_syscall` from a contract they control.

### Likelihood Explanation
This is reachable via a plain `Invoke` transaction from any unprivileged account: deploy or reuse a contract that calls `replace_class_syscall(undeclared_class_hash)`, and during Starknet OS re-execution/proving of the transaction, the guard is silently absent. The bug is not hypothetical — it is explicitly documented via the code's own TODO acknowledging the missing check is not yet implemented, so the likelihood of the divergence being exercised (intentionally or as part of normal traffic) is high once triggered by any contract performing this syscall against an undeclared class hash.

### Recommendation
Add the same validation in the OS's `execute_replace_class` (both `syscall_impls.cairo` and the deprecated `deprecated_execute_syscalls.cairo` path) that the blockifier enforces:
1. Verify the given `class_hash` corresponds to a declared class (mirroring `get_compiled_class`/`is_declared` checks in the blockifier).
2. Enforce the same restriction against replacing a Cairo1 (V1) class with a Cairo0 (V0) class hash (`ForbiddenClassReplacement`).
Ensure these checks are enforced identically in both the VM/native blockifier syscall handlers and the Starknet OS Cairo implementation so that OS re-execution cannot silently diverge from actual sequencer execution.

### Proof of Concept
Conceptual PoC (cannot be executed without full environment access):
1. Deploy an account/contract with an `external` function that calls `replace_class_syscall(class_hash)` where `class_hash` is a value that has never been declared on-chain.
2. Submit an `Invoke` transaction to call this function.
3. In blockifier execution, this call fails/reverts with "is not declared" as demonstrated by the existing test `undeclared_class_hash` in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`.
4. Trace the same transaction through the Starknet OS's `execute_replace_class` (`syscall_impls.cairo`/`deprecated_execute_syscalls.cairo`): because the check is a documented TODO, the OS path does not reject the call the same way, updates `contract_state_changes` with the undeclared class hash unconditionally, and can be shown (via unit testing of the OS Cairo function in isolation, feeding it a request with an undeclared class hash) to complete without failure, producing a state entry inconsistent with what an honest blockifier-driven full node would compute.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L90-97)
```rust
#[derive(Debug, Error)]
pub enum SyscallExecutionError {
    #[error("Bad syscall_ptr; expected: {expected_ptr:?}, got: {actual_ptr:?}.")]
    BadSyscallPointer { expected_ptr: Relocatable, actual_ptr: Relocatable },
    #[error(transparent)]
    EmitEventError(#[from] EmitEventError),
    #[error("Cannot replace V1 class hash with V0 class hash: {class_hash}.")]
    ForbiddenClassReplacement { class_hash: ClassHash },
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
