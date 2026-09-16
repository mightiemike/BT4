### Title
Starknet OS `execute_replace_class` omits the declared-class check that Blockifier enforces, allowing state divergence via `replace_class` syscall - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The external report's bug class ("code assumes an address/hash conforms to an expected type without verifying it") maps to a real interface-assumption gap in the Starknet OS's `replace_class` syscall implementation: it accepts an arbitrary `class_hash` and writes it into `contract_state_changes` without verifying the class is actually declared, whereas the Blockifier's native syscall handler for the same operation performs this check.

### Finding Description
In Blockifier, `replace_class` explicitly verifies the target class is declared before mutating state: [1](#0-0) 

The same guarantee is documented in the syscall error type (`ForbiddenClassReplacement`) and validated at `syscall_base.rs`'s `replace_class`, which is exercised by `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs` (tests `undeclared_class_hash` and `cairo0_class_hash` assert on "is not declared" / "Cannot replace V1 class hash with V0 class hash").

In contrast, the Starknet OS Cairo implementation that re-executes transactions to build/verify the block (used for state commitment and OS re-execution) performs the class-hash update unconditionally, with an explicit acknowledged TODO that the check is missing: [2](#0-1) 

The deprecated syscall path (`deprecated_execute_syscalls.cairo`) has the identical unchecked pattern: [3](#0-2) 

No other location in the OS Cairo code (searched for `is_declared`, `not declared`, `UndeclaredClassHash`, `assert_class_is_declared`, `get_compiled_class_hash`/`get_contract_class` around the syscall path) performs this validation before `execute_replace_class` is invoked from `deprecated_execute_syscalls.cairo:676-688`.

### Impact Explanation
This is an interface/consistency-assumption defect between the two Starknet execution engines that must produce identical state transitions: Blockifier (used for actual sequencing/proposing) and the Starknet OS Cairo program (used to re-execute the block and compute/verify the committed state root during proving). Because Blockifier rejects `replace_class` calls to undeclared or version-mismatched (V0/V1) class hashes, such a transaction never gets included in a block by an honest sequencer. However, if any other code path or future feature allows a transaction whose `replace_class` argument is undeclared to reach the OS re-execution (e.g., a change to Blockifier's guard, a different validation order, or an execution path that does not funnel through this exact check), the OS would silently accept it and update `contract_state_changes` with an unverified class hash, producing a state root that Blockifier itself could never have produced. This is exactly the class of "wrong committed root / honest-node divergence" risk the audit scope calls out, since the OS is the ultimate source of truth for the committed root used in proofs. The `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` comment explicitly documents that project maintainers consider this check to be currently missing and pending future implementation.

### Likelihood Explanation
Likelihood is Medium rather than High: under the current, intended call path, Blockifier's own check should prevent an undeclared-class `replace_class` transaction from ever being included in a block, so the OS's missing check is not directly reachable by a straightforward single malicious transaction today. However, the check is acknowledged as missing by the maintainers themselves (explicit TODO), the OS is meant to be an independent re-execution/verification layer (defense-in-depth), and any future change to Blockifier's validation order, entry-point dispatch, or a new call path (e.g., a new syscall variant, an internal/administrative bypass, or a bug elsewhere) could expose this gap without requiring a corresponding OS-side fix, given the two implementations are not guaranteed to stay in lockstep.

### Recommendation
Add the missing declared-class check inside `execute_replace_class` in `syscall_impls.cairo` (and its deprecated counterpart in `deprecated_execute_syscalls.cairo`), mirroring Blockifier's logic: verify the target `class_hash` corresponds to a declared class (and, if applicable, enforce the same V0/V1 replacement restriction Blockifier enforces via `ForbiddenClassReplacement`) before performing the `dict_update` on `contract_state_changes`. This closes the redundancy gap and ensures the OS cannot compute a state root that diverges from what an honest Blockifier-driven sequencer could produce, regardless of future changes to Blockifier's own validation ordering.

### Proof of Concept
Not directly exploitable via a single unprivileged transaction under the current code paths, since Blockifier's pre-check (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:795-807`, mirrored in `syscall_base.rs`/`syscall_executor.rs`) blocks inclusion of a `replace_class` call to an undeclared class before the OS ever re-executes it. The concrete PoC would require: (1) a future/alternate code path where a transaction invoking `replace_class` with an undeclared or version-mismatched class hash bypasses Blockifier's check yet still reaches OS re-execution, at which point `execute_replace_class` (`syscall_impls.cairo:881-920`) would unconditionally accept the hash and produce a `CHANGE_CLASS_ENTRY` revert-log/state update that Blockifier could never have produced, causing committed-root divergence. This cannot currently be demonstrated purely from the analog report without such a bypass, and the maintainers' own TODO comment is the strongest available evidence that this gap is real but not yet independently exploitable through the paths examined.

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
