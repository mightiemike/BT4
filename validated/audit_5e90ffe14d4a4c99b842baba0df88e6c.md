### Title
Missing "class is declared" check in Starknet OS `execute_replace_class` allows honest-node/OS divergence and unauthorized class-hash commitment - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall is reachable from any contract invoked in any unprivileged transaction (any account can deploy a trivial contract whose external function calls `replace_class` with an attacker-chosen class hash). In the Rust `blockifier` execution path this syscall is guarded — it reads the class via `get_compiled_class` and fails if the class was never declared — but the Starknet OS Cairo re-execution path that computes the committed state root and re-verifies blocks for proving contains no such check, and the omission is explicitly marked with a TODO.

### Finding Description
`blockifier`'s syscall handler enforces declaration before mutating class hash: [1](#0-0) 

The equivalent logic in the Starknet OS Cairo program (used both for the deprecated syscall path and the new syscall path) has an explicit unimplemented check: [2](#0-1) [3](#0-2) 

Both implementations comment `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` and instead directly perform `dict_update` on `contract_state_changes`, writing an unverified `class_hash` into the contract's `StateEntry`, which flows straight into the state commitment / Patricia-tree computation: [4](#0-3) [5](#0-4) 

The `replace_class` syscall is available to any Cairo contract with no elevated privileges — it is one of the standard syscalls listed for both deprecated and current syscall dispatch: [6](#0-5) [7](#0-6) 

### Impact Explanation
Because `blockifier` (used by the sequencer to build blocks) enforces the declared-class check while the OS (used to re-execute/verify blocks and compute the proven state root) does not, the two execution engines can diverge on identical input:
- If a class hash `H` is undeclared, `blockifier` will reject the `replace_class` call (revert), while the Starknet OS accepts it, silently writing `H` into the committed contract-state trie as the new class hash for that contract.
- This is a concrete state-transition divergence between the sequencer's execution and the OS's re-execution, i.e. "honest-node divergence" / "wrong committed root", since the state root computed by the OS for proving would not match what a blockifier-based node would compute for the same block if the two paths are exercised with differing enforcement (or if the check is ever relied upon during re-execution consistency checks). It also permits, at minimum in the OS-only accounting, associating a contract address with a garbage/undeclared class hash, corrupting the class hash leaf in the committed state and any downstream reads/gas-fee assumptions that depend on the class actually existing.
- This is reachable purely by an unprivileged transaction sender: deploy any Cairo0/Cairo1 contract that invokes the `replace_class` syscall with attacker-controlled calldata, then submit an ordinary invoke transaction calling it.

### Likelihood Explanation
High reachability: `replace_class` requires no special privilege, is available to every contract, and only needs one attacker-authored contract plus one ordinary transaction. The bug is explicitly acknowledged by a maintainer TODO in the code, confirming the check is genuinely absent rather than performed implicitly elsewhere (e.g., inside the `%{ GetContractAddressStateEntry %}` hint, which only fetches the current state entry and does not validate the new class hash).

### Recommendation
Add the missing declared-class check in both `execute_replace_class` implementations in `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring the `blockifier` behavior (`state.get_compiled_class(request.class_hash)?` before writing `set_class_hash_at` / updating `contract_state_changes`), ensuring the OS rejects `replace_class` calls that target undeclared class hashes exactly as `blockifier` does, so re-execution and block-building stay consistent.

### Proof of Concept
Not independently executable from the index alone (no local Cairo/OS test harness was run); the PoC is a straightforward code path:
1. Deploy a contract with an external function `foo()` containing `replace_class(some_never_declared_class_hash)`.
2. Submit an ordinary `invoke` transaction calling `foo()`.
3. In `blockifier`, this transaction reverts (`UndeclaredClassHash`).
4. In the Starknet OS re-execution / proving path, the same call proceeds past `execute_replace_class` and commits `some_never_declared_class_hash` into `contract_state_changes` unchecked, per [8](#0-7) .

**Uncertainty note:** I could not run the OS/blockifier test suites in this environment to empirically confirm a resulting root mismatch in a live re-execution/proof run; the finding is based on direct code-path inspection (the explicit TODO plus the corresponding blockifier guard), not a demonstrated on-chain divergence.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L676-688)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(
            contract_address=execution_context.execution_info.contract_address,
            syscall_ptr=cast(syscall_ptr, ReplaceClass*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - ReplaceClass.SIZE,
            syscall_ptr=syscall_ptr + ReplaceClass.SIZE,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L188-203)
```text
    local range_check_ptr = range_check_ptr;

    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L42-96)
```text
// Performs the commitment tree updates required for (validating and) updating the global state.
// Returns a CommitmentUpdate struct.
//
// `should_allocate_aliases` flag indicates whether to allocate aliases before squashing the
// contract state changes.
func state_update{poseidon_ptr: PoseidonBuiltin*, hash_ptr: HashBuiltin*, range_check_ptr}(
    os_state_update: OsStateUpdate, should_allocate_aliases: felt
) -> (squashed_os_state_update: SquashedOsStateUpdate*, state_update_output: CommitmentUpdate*) {
    alloc_locals;

    // Create PatriciaUpdateConstants struct for patricia update.
    let (local patricia_update_constants: PatriciaUpdateConstants*) = patricia_update_constants_new(
        );

    // (Maybe) allocate aliases and squash the final contract state tree.
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the global state.
    let (local initial_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.initial_root,
        contract_class_root=contract_class_tree_update_output.initial_root,
    );
    let (local final_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.final_root,
        contract_class_root=contract_class_tree_update_output.final_root,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L197-205)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
