### Title
Missing declared-class validation in Starknet OS `replace_class` syscall implementation - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Rekt report describes a case where a function that should have been access-gated (`withdrawStuckToken`) ended up moving user funds without proper restriction, because the check that should have prevented misuse was missing/bypassed. Searching the sequencer for an analogous "missing guard on a state-mutating entry point reachable by an ordinary caller," the closest concrete match is the `execute_replace_class` implementation used by the Starknet OS Cairo program, which — unlike the equivalent Rust `blockifier` implementation — does not verify that the target `class_hash` corresponds to an actually declared class before committing the class-hash update to contract state.

### Finding Description
Any contract can invoke the `replace_class` syscall as part of a normal, unprivileged transaction to change its own class hash. In the Rust `blockifier` execution path, this is properly gated: the deprecated (Cairo0) syscall handler explicitly requires the class to be declared before allowing the update: [1](#0-0) 

However, the Starknet OS Cairo program — which independently re-executes syscalls (used in Starknet OS re-execution / proof generation) — implements `execute_replace_class` for both the Cairo1 syscall path and the deprecated Cairo0 path without this declared-class check. The Cairo1 OS implementation contains an explicit `TODO` acknowledging the missing check: [2](#0-1) 

The deprecated (Cairo0) OS syscall path shows the same pattern — it updates the `StateEntry.class_hash` directly via `dict_update` with no verification that the class was ever declared: [3](#0-2) 

This is a validation asymmetry between the two engines that are meant to compute identical state transitions for the same transaction: `blockifier` (used by the sequencer/batcher to build blocks) enforces "class must be declared," while the `starknet_os` Cairo program (used to prove/re-execute the same transactions for the committed state root and block hash) does not enforce this invariant on its own.

### Impact Explanation
If any transaction path exists where the OS re-executes a `replace_class` syscall against a `class_hash` that was not properly gated the same way blockifier gates it (e.g., differences in when/how the check is applied, or future code paths that call the OS's `execute_replace_class` independent of blockifier's pre-validated state diff), the OS could commit a `StateEntry` with a class hash that does not correspond to any declared class. Since the OS's execution output feeds directly into the Patricia tree / state commitment and block hash computation, this could allow a discrepancy between what a client using `blockifier` alone would compute versus what gets proven and committed by the OS, i.e., a wrong committed root, or accepting a contract class-hash assignment that the "reference" execution engine (blockifier) would have rejected. This falls under "wrong committed root" / "honest-node divergence" categories in scope.

### Likelihood Explanation
Reachability is high in principle — any account contract can call `replace_class` in a plain invoke transaction, with no special privilege required. However, I was not able to fully trace, within the available time, whether the OS's `execute_replace_class` is ever invoked on data that has not already been pre-validated by `blockifier` (i.e., whether blockifier's stricter check fully shields the OS from ever seeing an undeclared class hash in practice). The explicit developer `TODO` comment in the source confirms the maintainers are aware the check is currently absent, which supports that this is a real, currently-unaddressed gap in the OS's syscall semantics rather than a hypothetical one.

### Recommendation
Add the same "class must be declared" check to `execute_replace_class` in both `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo` and the deprecated variant in `deprecated_execute_syscalls.cairo`, mirroring the check already performed in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs` (`syscall_handler.state.get_compiled_class(request.class_hash)?`), so that the OS's re-execution semantics cannot diverge from blockifier's enforced invariants regardless of how or when the OS syscall implementation is invoked.

### Proof of Concept
Not independently verifiable from the indexed code alone — a concrete PoC would require confirming whether the OS ever executes `replace_class` on a `class_hash` that bypassed blockifier's declared-class check (e.g., via a divergent input path into `starknet_os` re-execution). This should be validated with the actual repository/tests (e.g., `starknet_os` re-execution test harnesses) in a full development environment, since the ask-only index has limited visibility into how `blockifier`-produced state diffs are wired into the OS's syscall re-execution inputs. [4](#0-3)

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
