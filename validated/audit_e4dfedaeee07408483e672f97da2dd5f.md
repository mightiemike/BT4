Based on the investigation, I found a genuine analog to the "corruptible upgradability" pattern — but manifested in the Starknet OS's re-execution of the `replace_class` syscall rather than in Solidity storage layout.

### Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes OS/Blockifier execution divergence on class-hash upgrade - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall is Starknet's native "upgrade" mechanism — an account/contract can swap its class hash to point at new code, analogous to `upgradeTo` in the Ethos proxy pattern from the external report. In the Rust `blockifier`, before performing the class swap the implementation validates the target class is actually declared by calling `get_compiled_class`, and only then updates the contract's class hash. The Cairo re-implementation of the same syscall inside the Starknet OS (used for block re-execution/proving) skips this validation entirely, with an explicit unresolved `TODO` acknowledging the gap.

### Finding Description
In blockifier's deprecated syscall handler, `replace_class` first fetches/validates the compiled class before mutating state: [1](#0-0) 

The non-deprecated blockifier syscall path delegates to the same base logic (`syscall_handler.base.replace_class(...)`): [2](#0-1) 

In contrast, the Starknet OS's Cairo implementation of the same syscall (used during OS/SNOS re-execution to verify and generate the state-transition proof) updates the `StateEntry`'s `class_hash` directly, with only a `TODO` marking the missing declared-class check — the check itself is not present: [3](#0-2) 

The deprecated-syscall OS path (`deprecated_execute_syscalls.cairo`) does not even carry the `TODO` — the check is absent altogether: [4](#0-3) 

This is precisely the "inheriting non-upgrade-safe/unvalidated component" root cause described in the report: two independently maintained implementations of the same state-mutating operation (`replace_class`), one (blockifier) enforcing a precondition and one (Starknet OS) not enforcing it. Since the OS is the canonical logic replayed for STARK proving/validation of the sequencer's committed block, any semantic gap between it and blockifier is a soundness-relevant divergence rather than a cosmetic one.

### Impact Explanation
If the OS's `execute_replace_class` diverges from blockifier's behavior for any input where declared-class status matters (e.g., a class hash that is not declared, or has been declared in a way not yet visible/consistent with the OS's view of `contract_state_changes`), the state diff the OS computes when replaying the block would set a `class_hash` to a value that blockifier's real execution would have rejected. This is an "honest-node divergence" between the block-producing component (blockifier) and the block-proving/verifying component (Starknet OS) over the resulting committed class hash, which underlies contract code resolution for all subsequent calls to that contract address — a wrong committed root/class assignment.

### Likelihood Explanation
Any account or contract can invoke a contract that calls `replace_class(class_hash)` with an arbitrary felt via a single ordinary transaction — no special privileges required. Whether the divergence is exploitable end-to-end depends on whether some legitimate flow can produce a class hash that is accepted by the Cairo `%{ GetContractAddressStateEntry %}` hint machinery but was never validated as declared; because the check is unconditionally absent in the OS path (not merely gated behind a flag), the missing validation is present on every `replace_class` call replayed by the OS today, which is a design gap the team has already flagged with an open `TODO` rather than fixed.

### Recommendation
Add the same "class is declared" validation to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` that blockifier performs via `get_compiled_class`/`set_class_hash_at`, so the OS enforces an identical precondition set as the Rust execution layer before committing to a new `class_hash` in `StateEntry`.

### Proof of Concept
1. Deploy a contract that calls the `replace_class` syscall with a `class_hash` value that has not been declared.
2. In blockifier (real block execution), `execute_replace_class`/`replace_class` calls `state.get_compiled_class(request.class_hash)` first ( [5](#0-4) ), which errors out for an undeclared class, causing the transaction to revert.
3. When the Starknet OS re-executes/proves the same block, `execute_replace_class` ( [6](#0-5) ) performs no equivalent declared-class check before writing the new `StateEntry`, so any Cairo-level code path that reaches this function without blockifier's Rust-side guard (e.g., a future/alternate execution engine, or a code path where the Rust guard is bypassed/changed) would silently accept and commit an unvalidated class-hash swap, diverging from blockifier's rejection.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L685-693)
```rust
    fn replace_class(
        request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        syscall_handler.base.replace_class(request.class_hash)?;
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
