## Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes state divergence from blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Cairo implementation of the `replace_class` syscall inside the Starknet OS program does not verify that the target class hash is an actually-declared class before overwriting a contract's class hash in `contract_state_changes`. The Rust `blockifier` implementation used by the sequencer to execute transactions performs this check and rejects the syscall otherwise, but the OS's Cairo re-execution path — used to produce the provable execution trace / state diff for a block — omits it, as explicitly flagged by an unresolved TODO in the code.

### Finding Description
`blockifier`'s `replace_class` syscall implementation explicitly requires the class to be declared (and to be Cairo1/V1) before it mutates state: [1](#0-0) 

This is exercised and enforced by tests, which assert that calling `replace_class` with an undeclared class hash fails with `"is not declared"`, and with a Cairo0 hash fails with `"Cannot replace V1 class hash with V0 class hash"`: [2](#0-1) 

The deprecated (Cairo0) syscall handler in blockifier performs the equivalent check via `get_compiled_class`: [3](#0-2) 

However, the Starknet OS's own Cairo implementation of the same syscall, used for the block's provable re-execution, sets the new class hash directly on `contract_state_changes` without any equivalent check — the code contains an explicit acknowledgment that this validation is missing: [4](#0-3) 

The legacy/deprecated OS syscall path (`ReplaceClass` for Cairo0 callers) has the identical gap — no declared-class check before the `dict_update` that commits the new class hash: [5](#0-4) 

Because the OS is the Cairo-provable re-execution engine of the sequencer's block (the "Starknet OS re-execution" scope explicitly listed as in-scope), any divergence between what `blockifier` computes (the state diff the sequencer commits and gossips) and what the OS computes when re-executing the same transaction is a consensus-critical bug: the two engines are supposed to compute identical state transitions for every transaction.

### Impact Explanation
An unprivileged transaction sender can deploy/invoke a contract that calls the `replace_class` syscall with an undeclared class hash (or, in the legacy path, any Cairo0 caller doing the same). Under `blockifier`, this syscall fails and the transaction reverts, so no class-hash state change is committed by the sequencer. Under the OS's Cairo re-execution, the same syscall call succeeds silently and writes the (fictitious/undeclared) class hash into `contract_state_changes`, which subsequently affects the computed state diff, the committed state root, and ultimately the block hash produced by the OS/proving pipeline. This produces an honest-node/engine divergence: the state root and block artifacts computed by the OS re-execution differ from the ones computed and committed by blockifier for the identical block/transaction, which can prevent proof/consensus agreement on the block, or in the worst case allow a wrong state root to be treated as valid by any component relying on OS re-execution instead of blockifier's result.

### Likelihood Explanation
The trigger is a single `replace_class` syscall call passing an arbitrary (undeclared) `class_hash`, reachable trivially by any account/contract executing an ordinary invoke transaction — no special privileges, staking, or malicious-operator behavior are required. Given `replace_class` is a very commonly available and simple syscall, and the described gap is an explicit unresolved TODO in the shipped code, the condition is easy to hit either accidentally (buggy user contract) or deliberately.

### Recommendation
Add the same declared-class (and same-version, i.e. Cairo0 vs Cairo1) validation to the OS Cairo `execute_replace_class` implementations (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that `blockifier`'s `replace_class` performs in `syscall_base.rs` and `hint_processor.rs`, ensuring the OS rejects (or reverts, matching blockifier's error/revert semantics) any `replace_class` syscall targeting a class hash that is not declared, before committing the new `StateEntry` to `contract_state_changes`.

### Proof of Concept
1. Deploy a contract exposing a `replace_class`-invoking entry point (e.g., the existing `test_replace_class` function used in blockifier's test suite).
2. Submit an invoke transaction calling this entry point with an arbitrary, undeclared `class_hash`.
3. Observe: `blockifier`-based sequencer execution reverts the transaction (`"... is not declared"`), as shown in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:15-29` and `crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs:375-391`.
4. Feed the identical transaction/trace to the Starknet OS Cairo re-execution (`execute_replace_class` in `syscall_impls.cairo:881-920` / `deprecated_execute_syscalls.cairo:307-329`): the syscall succeeds and updates `contract_state_changes` with the undeclared class hash, since no declared-class check exists in that path — producing a state diff/root that disagrees with blockifier's result for the same transaction.

### Citations

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
