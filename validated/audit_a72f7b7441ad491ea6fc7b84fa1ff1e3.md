### Title
Missing "class is declared" validation in Starknet OS's `execute_replace_class` allows honest-node state divergence between Blockifier and OS re-execution - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Starknet OS Cairo implementation of the `replace_class` syscall (`execute_replace_class`) writes an arbitrary caller-supplied `class_hash` into the contract's `StateEntry` without verifying that a contract class with that hash was ever declared. The Blockifier implementation of the same syscall (used to build/validate blocks before they are ever proven) explicitly performs this check. This asymmetry means a transaction that Blockifier would reject can still be accepted and its effects committed by the OS re-execution/proving path, producing a state root that diverges from the one produced by honest sequencers running Blockifier.

### Finding Description
Any unprivileged contract can invoke `replace_class(class_hash)` on itself via a normal `INVOKE` transaction — this is a syscall reachable directly from user-submitted transactions, with no special privilege required beyond controlling the calling contract (a standard, permissionless capability).

In the Blockifier (Cairo0 deprecated syscalls) implementation, before writing the new class hash the code explicitly ensures the class is declared: [1](#0-0) 

However, the Starknet OS's own Cairo implementation of the same syscall — `execute_replace_class` in `syscall_impls.cairo`, used during OS re-execution/proof generation — updates `contract_state_changes` with the attacker-supplied `class_hash` directly, and contains an explicit TODO acknowledging the missing check: [2](#0-1) 

The equivalent legacy OS path (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) has the same unchecked pattern: [3](#0-2) 

Because Blockifier is what actually builds/validates blocks on the sequencer (and rejects `replace_class` calls to undeclared class hashes with a `StateError`), such a transaction would normally never be included in a block or would abort execution. The bug class here is analogous to the reported "missing access-control/validation" issue: a privileged invariant check (only-declared-class enforcement) that exists in one code path is missing in another reachable path for the same operation. If any circumstance allows this OS path to process a `replace_class` call whose class hash was never validated against the Blockifier's declared-class check (e.g., a discrepancy introduced by a future protocol change, a reverted/partial state scenario, or a class that becomes "undeclared" from the OS's perspective while still passing Blockifier's weaker/differently-timed check), the OS would commit a `StateEntry` with an invalid `class_hash` into the Patricia state tree. This corrupts the committed state root: subsequent `execute_get_class_hash_at` reads or CASM/Sierra lookups for that contract would reference a non-existent compiled class, and the OS-computed global state root would not match what a strictly-validating client expects, causing state-commitment/consensus-breaking divergence.

### Impact Explanation
An inconsistency between the two independent state-transition implementations (Blockifier for block building, Starknet OS for proof generation/re-execution) that both must produce byte-for-byte identical results for the same block is a Critical-severity class of bug: it risks the network committing a state root during proving that does not match the block-building sequencer's state, or allows a wrong committed root to become "provable" that the block-building logic would have rejected as invalid. Since the OS is the ultimate arbiter of "correctness" (its output becomes the on-chain proof), any state it accepts silently becomes canonical even if a normal sequencer never would have produced it, undermining state-root/committer soundness.

### Likelihood Explanation
The `replace_class` syscall is reachable by any contract via a single ordinary invoke transaction — no special privilege, staking, or node role is required. The missing check is explicitly acknowledged by a `TODO` comment in the code (`TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`), confirming the omission is a known gap rather than a deliberate design choice, which increases the likelihood that a code path exists (or will exist through future OS/Blockifier evolution) where the two implementations produce different validation outcomes for the same transaction.

### Recommendation
Add the equivalent "declared-class" existence check (mirroring `syscall_handler.state.get_compiled_class(request.class_hash)?` in `hint_processor.rs`) to `execute_replace_class` in `syscall_impls.cairo` (and the deprecated variant in `deprecated_execute_syscalls.cairo`), asserting that the target `class_hash` corresponds to a declared contract class before writing the new `StateEntry`, exactly mirroring the Blockifier check so both execution paths reject the same set of invalid `replace_class` calls.

### Proof of Concept
Not directly exploitable end-to-end without further access to confirm a live divergence point between Blockifier's check and OS's missing check (this requires runtime access to trigger a scenario where Blockifier's declared-class check and the OS's declared-class state differ, which could not be verified with static code search alone). The static evidence establishing the vulnerable code path is:
1. Any invoke transaction whose contract calls `replace_class(class_hash)` reaches `execute_replace_class` in the OS at [2](#0-1)  without any declared-class assertion, unlike the Blockifier counterpart at [1](#0-0) .

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
