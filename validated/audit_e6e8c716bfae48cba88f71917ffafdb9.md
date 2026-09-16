## Finding

The Liferay bug is a missing-access-control check (CWE-862) that lets an operation proceed without verifying the caller's right to it. The closest analog in this Starknet sequencer codebase is a missing declared-class validation in the Starknet OS's `replace_class` syscall implementation, which diverges from the equivalent (and correctly checked) blockifier implementation.

### Title
Starknet OS `replace_class` syscall skips the declared-class check enforced by the blockifier, causing execution/state divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The blockifier's implementation of the `replace_class` syscall requires that the target class hash correspond to an actually declared class before permitting a contract to replace its own class hash [1](#0-0) . The Starknet OS re-execution path (used to generate the block's STARK proof and to independently compute state/class commitments) implements the same syscall but explicitly skips this check, marked with a `TODO`, both in the new syscall handler and the deprecated one [2](#0-1) [3](#0-2) .

### Finding Description
Any deployed contract can invoke the `replace_class` syscall with an arbitrary class hash to change its own `class_hash` entry in state. In the blockifier (the code that actually executes the block and determines transaction success/failure and the committed state diff), this call is guarded:
```rust
fn replace_class(...) -> DeprecatedSyscallResult<ReplaceClassResponse> {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler.state.set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;
    Ok(ReplaceClassResponse {})
}
``` [1](#0-0) 

If `request.class_hash` is not declared, `get_compiled_class` returns `StateError::UndeclaredClassHash`, causing the syscall — and typically the entire transaction — to fail/revert. The same holds for the new (non-deprecated) syscall path, which delegates to `syscall_handler.base.replace_class(request.class_hash)` [4](#0-3) .

However, the Cairo implementation used by the Starknet OS (SNOS) — which re-executes the block to build the state commitment and produce the proof — performs no such check:
```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
dict_update{dict_ptr=contract_state_changes}(...)
``` [2](#0-1) 

The identical gap exists in the deprecated syscall handler `execute_replace_class` [3](#0-2) . The Rust-side `snos_deprecated_syscall_executor` and `snos_syscall_executor` "trusted" fast paths similarly return `Ok(ReplaceClassResponse {})` unconditionally without validation [5](#0-4) [6](#0-5) .

Because these class-hash updates feed directly into `contract_state_changes`, which is squashed and hashed into the contract state tree and then into the global state root that the OS commits and outputs as the new block hash [7](#0-6) , any divergence between what the blockifier actually accepts/reverts and what the OS accepts unconditionally becomes baked into the state/commitment path used for proving.

### Impact Explanation
The blockifier is the authority for what actually gets committed to the chain (it decides revert vs. success and produces the real state diff). The Starknet OS is expected to faithfully re-execute the same transactions to prove that diff is correct. Because the OS's `replace_class` never rejects an undeclared class hash while the blockifier does, a transaction that calls `replace_class` with an undeclared class hash will be handled inconsistently between the two execution engines (blockifier reverts/fails the inner call; OS accepts it and updates `contract_state_changes` with the undeclared class hash unconditionally). This is a concrete case of "honest-node divergence" / risk of a wrong committed root, since the component responsible for generating/validating the proof of the block's state transition does not enforce a constraint that the actual state-transition-determining component enforces.

### Likelihood Explanation
Triggering the divergent code path requires only a single ordinary transaction: any deployed contract can call the public `replace_class`/`ReplaceClass` syscall with an arbitrary (undeclared) class hash — no elevated privileges, no operator/prover collusion, and no special contract deployment. The reachable path (regular invoke transaction → contract entry point → `replace_class` syscall) is entirely within the scope of "a single submitted transaction" reachable by an unprivileged sender.

### Recommendation
Add the same declared-class validation to the Starknet OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) and to the SNOS Rust syscall executors, mirroring the blockifier's `get_compiled_class` check, so that the OS rejects (or reverts consistently with the blockifier) any `replace_class` call referencing an undeclared class hash before that value is written into `contract_state_changes`.

### Proof of Concept
1. Deploy any contract exposing an entry point that calls the `replace_class` syscall with a caller-supplied `class_hash` (e.g. the existing test helper `test_replace_class` pattern) [8](#0-7) .
2. Submit an invoke transaction calling this entry point with an arbitrary, never-declared `class_hash` value.
3. Observe that blockifier execution fails/reverts this call because `get_compiled_class` returns `StateError::UndeclaredClassHash` [9](#0-8) .
4. Feed the same transaction into the Starknet OS re-execution path and observe that `execute_replace_class` unconditionally updates `contract_state_changes` with the undeclared `class_hash`, with no equivalent failure, per the code at [10](#0-9) .
5. The divergent per-transaction outcome propagates into differing `contract_state_changes`/class-tree commitments between the two independent execution engines.

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

**File:** crates/starknet_os/src/hint_processor/snos_deprecated_syscall_executor.rs (L365-371)
```rust
    fn replace_class(
        _request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        _syscall_handler: &mut Self,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/starknet_os/src/hint_processor/snos_syscall_executor.rs (L319-326)
```rust
    fn replace_class(
        _request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        _syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L42-66)
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
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
