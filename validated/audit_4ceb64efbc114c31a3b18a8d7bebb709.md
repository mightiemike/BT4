Based on my research, I did not find a valid analog matching the strict criteria of the report (an unprivileged actor invoking a function that should require an operator/privileged role but doesn't). However, I did surface a related but distinct issue in the `replace_class` syscall handling that is worth flagging with appropriate caveats about incomplete verification.

### Title
Missing declared-class check in Starknet OS `execute_replace_class` diverges from blockifier's enforced check - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall is callable by any contract during execution of any transaction from an unprivileged sender (any invoke/l1-handler call that reaches a contract executing `replace_class_syscall`). In the blockifier's deprecated syscall implementation, the class hash being replaced-to is validated to be actually declared before the state change is applied. In the Starknet OS Cairo re-execution path, this same validation is explicitly marked as **not yet implemented** via a `TODO` comment.

### Finding Description
The blockifier's deprecated syscall handler for `replace_class` enforces that the target class hash is declared before mutating state: [1](#0-0) 

In contrast, the Starknet OS's Cairo implementation of the same syscall (`execute_replace_class` in `syscall_impls.cairo`) contains an explicit TODO acknowledging the check is missing: [2](#0-1) 

The OS variant simply reads the current `StateEntry`, builds a `new_state_entry` with the requested (unvalidated) `class_hash`, and commits the state-diff update — with no check that a contract class was ever declared for that hash.

### Impact Explanation
If a transaction from an unprivileged sender calls `replace_class` with an undeclared class hash:
- The blockifier (used for block building/mempool validation and normal node execution) would reject/revert the syscall because `get_compiled_class` fails for an undeclared class.
- The Starknet OS re-execution path (used to generate/verify proofs and to re-execute blocks, per the "Starknet OS Re-execution" component) would accept the same transaction and commit a different resulting class hash for the contract, since it performs no such validation.

This is a state-transition-function divergence between the two independently-implemented execution engines that are both supposed to compute identical state diffs for the same transaction. Such a divergence can result in the OS committing a different state root than the blockifier-produced block, i.e. an honest-node/execution-engine divergence and potential wrong committed root — one of the explicitly accepted high-severity impact categories for this scan.

### Likelihood Explanation
Likelihood cannot be fully confirmed without further verification of two open items:
1. Whether the regular (non-deprecated) blockifier syscall path (`crates/blockifier/src/execution/syscalls/hint_processor.rs:685-693`, which delegates to `syscall_handler.base.replace_class`) performs the same declared-class check — I was not able to retrieve the body of `base.replace_class` in `syscall_base.rs` before running out of tool budget.
2. Whether some other mechanism (e.g., commitment-tree consistency checks, or a later validation stage in the OS) effectively prevents an undeclared class hash from being committed, mitigating the gap noted by the TODO.

Given the TODO is dated for a **future** date (`1/1/2026`) in the comment itself, it strongly suggests this is a known, currently-unaddressed gap in the OS rather than a stylistic omission, which raises confidence that the divergence is real, but I could not fully trace the downstream consequences (e.g., whether the final block-hash/commitment step catches the mismatch).

### Recommendation
Add the same "class hash must be declared" check to `execute_replace_class` in `syscall_impls.cairo` (and its deprecated counterpart in `deprecated_execute_syscalls.cairo`, if not already covered) that mirrors the blockifier's `get_compiled_class` check, ensuring both execution engines reject `replace_class` calls to undeclared class hashes identically.

### Proof of Concept
Conceptual PoC (not independently executed):
1. Deploy a contract implementing `test_replace_class` similar to the feature contract in `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo:513-516`.
2. Submit an invoke transaction from any account calling `test_replace_class` with a `class_hash` value that has never been declared on-chain.
3. Run the transaction through the blockifier (gateway/batcher execution) — expect rejection/revert due to `get_compiled_class` failing on the undeclared hash.
4. Run the same block/transaction through the Starknet OS re-execution (used for proof generation), following `execute_replace_class` in `syscall_impls.cairo` — the state diff is applied unconditionally, producing a class-hash state entry for a class that was never declared, with no equivalent rejection. [1](#0-0) [2](#0-1) 

**Note on confidence:** This finding is offered with moderate confidence only, since I could not fully verify the non-deprecated blockifier `base.replace_class` implementation nor whether a downstream check (e.g., during block commitment) neutralizes the OS gap. If you need certainty on those two points, a follow-up session with direct file access to `crates/blockifier/src/execution/syscalls/syscall_base.rs` and the block-commitment logic in `crates/apollo_committer` would resolve the remaining uncertainty.

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
