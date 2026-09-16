### Title
Missing validation of `class_hash` in `execute_replace_class` (Starknet OS syscall re-execution) causes state divergence from the Blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall handler in the Starknet OS Cairo program (`execute_replace_class`) unconditionally writes the caller-supplied `class_hash` into the contract's state entry without checking that the class is actually declared. This contradicts the Rust Blockifier implementation of the same syscall, which explicitly requires the class to be declared (and, for the new syscall version, of Cairo1 type) before allowing the replacement. Because the OS is the code that re-executes transactions to produce the committed state root/block hash, this discrepancy is a state-computation divergence, not merely a missing-check bug in an isolated contract.

### Finding Description
The Blockifier's `replace_class` syscall implementation validates the class hash before mutating state: [1](#0-0) 
and the legacy (Cairo0) syscall path does the same: [2](#0-1) 
Both call `state.get_compiled_class(class_hash)?` first, which returns `StateError::UndeclaredClassHash` (causing the syscall/transaction to fail) if the class was never declared, as confirmed by the syscall tests: [3](#0-2) 

However, the Starknet OS Cairo re-execution logic for the same syscall performs the state write directly, with an explicit acknowledgment that the check is missing: [4](#0-3) 
The comment `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` at line 902 confirms the validation gap. The deprecated (Cairo0) OS syscall handler has the identical gap: [5](#0-4) 

Because the OS is the trusted component that re-executes a block's transactions to derive the state diff/commitment used for the block hash and STARK proof, any code path where the OS accepts a state transition the Blockifier would have rejected (or vice-versa) results in the OS computing a different resulting state than what the sequencer actually committed.

### Impact Explanation
Any contract can trivially reach this code by invoking the `replace_class` syscall with an arbitrary, undeclared `class_hash`:
- In the sequencer's Blockifier, this call reverts (`is not declared`) and the contract's class hash is left untouched in the state diff.
- In the Starknet OS re-execution, the same call succeeds and rewrites the contract's `class_hash` state entry to the arbitrary, undeclared value, producing a different state diff.

This causes the OS-computed state commitment/root and block hash to diverge from the state actually produced and committed by the sequencer, satisfying "wrong committed root or block hash" / "honest-node divergence" criteria. Once such a contract exists with a bogus class hash committed at the OS level, any subsequent call into that contract address (which the OS believes points to an undeclared/nonexistent class) will behave inconsistently between the two systems, and the mismatch also breaks the soundness of the STARK proof that is supposed to attest to the sequencer's actual execution.

### Likelihood Explanation
Trivially reachable: any account or contract can issue an `INVOKE` transaction that calls a contract exposing `replace_class`(class_hash) with an arbitrary felt as `class_hash` (no special privileges, no declare needed, single transaction). This makes the divergence deterministically triggerable by any unprivileged transaction sender.

### Recommendation
Add the missing declared-class validation to `execute_replace_class` in both the current and deprecated Starknet OS syscall handlers (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), mirroring the Blockifier's check: look up `class_hash` in `contract_class_changes` (and/or the class hash storage abstraction that models `get_compiled_class`) and fail/revert the syscall if the class is undeclared, matching the Blockifier's `StateError::UndeclaredClassHash` behavior exactly (including the Cairo1-only restriction enforced in `syscall_base.rs::replace_class`, lines 373-375).

### Proof of Concept
1. Deploy a contract `A` whose Cairo code, when invoked, calls the `replace_class` syscall with a hardcoded, never-declared `class_hash` value (e.g., `0x1234`).
2. Submit an `INVOKE` transaction calling that entry point on `A`.
3. Observe that the Blockifier (sequencer execution) reverts the transaction with `"... is not declared"` (see `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs` lines 17-29), leaving `A`'s class hash unchanged in the committed state diff.
4. Feed the same transaction/block into the Starknet OS re-execution flow (`execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo`): because no declared-class check exists there, the OS accepts the syscall and updates `A`'s `state_entry.class_hash` to `0x1234` in `contract_state_changes`.
5. Compare the two resulting state diffs/roots — they differ, demonstrating the state/commitment divergence caused by the missing validation.

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
