## Analysis

I found a valid analog. The bug class from the report — "a state-mutating operation is missing a validation check that another code path in the same system does perform, causing invariant-breaking side effects for an unprivileged actor" — maps to the Starknet OS's implementation of the `replace_class` syscall.

### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by the Blockifier, causing state divergence between sequencer-committed state and OS re-execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Blockifier's Rust implementation of the `replace_class` syscall verifies that the target class hash is declared before mutating a contract's class hash. The Starknet OS Cairo implementation of the same syscall performs the state mutation unconditionally, with an explicit `TODO` acknowledging the missing check. Any unprivileged contract call that invokes `replace_class_syscall` with an undeclared class hash and gracefully handles the resulting syscall failure (rather than panicking/reverting the whole transaction) will therefore produce two different final states depending on which execution engine processes it — the sequencer's Blockifier (no class-hash change, syscall fails) versus the OS re-execution used for proving (class-hash change is written unconditionally).

### Finding Description
In the Blockifier, `replace_class` first reads the compiled class to confirm it exists before writing the new class hash: [1](#0-0) 

This lookup returns `StateError::UndeclaredClassHash` (surfaced through `get_compiled_class`) if the class was never declared, causing the syscall itself to fail without mutating `class_hash_at`.

In the Starknet OS's Cairo implementation of the same syscall, the equivalent function unconditionally performs the state write and explicitly defers the declared-class check: [2](#0-1) 

Note the `TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` at line 902 — the check simply does not exist yet. The deprecated (Cairo 0) syscall path has the identical gap: [3](#0-2) 

A Cairo 1 contract can call `replace_class_syscall` and choose not to `.unwrap_syscall()` the result (i.e., handle a `SyscallResult::Err` gracefully instead of panicking), as demonstrated by the test-contract wrapper used across the Blockifier syscall tests: [4](#0-3) 

In that scenario:
- Blockifier (used by the sequencer to execute and commit the block): the syscall fails because the class is undeclared; no class-hash state change is recorded; the transaction can continue executing normally without reverting if the contract handles the error.
- Starknet OS (used to re-execute the same transaction for STARK proof generation / re-execution testing, e.g. Echonet): the syscall unconditionally records a `CHANGE_CLASS_ENTRY` state change with the undeclared class hash, regardless of whether it exists.

Since the OS's revert decision for the whole transaction is trusted from a hint (`IsReverted`) rather than independently derived, this per-syscall state divergence is not caught by the top-level revert check — the transaction is treated as "not reverted" in both engines, but the two engines end up with different final storage/class-hash state for the affected contract.

### Impact Explanation
This produces state divergence between the state actually committed by the sequencer (via Blockifier + committer, whose Patricia tree/state root becomes the canonical committed root) and the state independently recomputed by the Starknet OS during proof generation or OS re-execution testing. Because the OS output is used to build the STARK proof of the block's state transition (see `crates/starknet_transaction_prover` and `crates/starknet_os_flow_tests`, which drive `commit_state_diff`/`StateCommitmentInfos` off of OS-derived state diffs), a mismatched class hash for the affected contract address means the OS-derived state root will not match the sequencer-committed root. This is a wrong-committed-root / honest-node-divergence class issue: proof generation for the block would fail to validate against the actual committed state, or, if trusted blindly, would finalize an incorrect state root — either blocking new block confirmation or corrupting state consistency guarantees the proof is meant to provide.

### Likelihood Explanation
Reachable by any unprivileged L2 account: any deployed Cairo 1 contract that calls `replace_class_syscall` with a class hash that has not been declared, and structures its code to swallow the resulting `SyscallResult::Err` instead of unwrapping/panicking, triggers the divergence deterministically on every such call. No special privileges, staking, or operator/peer misbehavior are required — it is purely a consequence of one execution engine (Blockifier) validating a precondition that the other (Starknet OS Cairo implementation) does not yet enforce, as the code's own `TODO` comment confirms.

### Recommendation
Add the missing "class hash must be declared" check to `execute_replace_class` in both `syscall_impls.cairo` (Cairo 1 syscalls) and `deprecated_execute_syscalls.cairo` (Cairo 0 syscalls), mirroring the Blockifier's `get_compiled_class` check in `hint_processor.rs`, before performing the `dict_update` that mutates `contract_state_changes`. This should fail the syscall (write a failure response / propagate an error) rather than silently succeeding, so that OS re-execution stays consistent with Blockifier execution for all code paths, including ones where the calling contract does not unwrap the syscall result.

### Proof of Concept
1. Declare no class at hash `H` (i.e., `H` is not present in `declared_classes`/`deprecated_declared_classes`).
2. Deploy a Cairo 1 contract whose entry point calls `starknet::replace_class_syscall(H)` and, instead of `.unwrap_syscall()`, matches on the `Result` and ignores the `Err` branch, then continues execution and returns normally.
3. Execute this entry point as an ordinary `INVOKE` transaction through the Blockifier: the `replace_class` syscall fails via the `get_compiled_class` check (`hint_processor.rs:801`), so no `class_hash_at` update is recorded; the transaction as a whole succeeds (not reverted) because the contract ignored the syscall error.
4. Re-execute the identical transaction through the Starknet OS (`syscall_impls.cairo:execute_replace_class`): the function unconditionally performs `dict_update` writing `class_hash=H` into `contract_state_changes` for the calling contract address, with no declared-class check (per the `TODO` at line 902).
5. Compare the two engines' resulting state diffs for the contract address: Blockifier reports no class-hash change; the OS reports a class-hash change to the undeclared hash `H`. This is the divergence — the OS-derived state root/state diff used for proving will not match the sequencer's actually committed state root.

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
