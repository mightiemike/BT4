### Title
Missing declared-class validation in Starknet OS `execute_replace_class` allows setting an undeclared class hash — ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `execute_replace_class` function in the Starknet OS (SNOS) Cairo program writes an arbitrary, attacker-supplied `class_hash` into a contract's state entry without ever checking that this class hash corresponds to a class that has actually been declared. The check is explicitly called out as missing via a `TODO` comment, and it is inconsistent with the blockifier's own Rust implementation of the same syscall, which does perform this validation.

### Finding Description
The `replace_class` syscall lets a contract change its own class hash. In the blockifier (the sequencer's execution engine), this is implemented with an explicit "is declared" check before the class hash is written to state: [1](#0-0) 

```
fn replace_class(...) -> DeprecatedSyscallResult<ReplaceClassResponse> {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler.state.set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;
    Ok(ReplaceClassResponse {})
}
```

However, the equivalent logic in the Starknet OS Cairo program (used for block re-execution / proving, i.e. `apollo_starknet_os_program`) omits this check entirely: [2](#0-1) 

The relevant lines are:
```
let class_hash = request.class_hash;

// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
```

This is structurally the same bug class described in the external report: a downstream consumer (`execute_replace_class`) accepts and persists a value (`class_hash`) into a state structure without validating that the value satisfies the invariant the rest of the system relies on (that it corresponds to a class that was actually declared). The TODO comment itself confirms this is a known, unaddressed gap.

### Impact Explanation
Because the blockifier enforces the "is declared" check but the OS does not, there is an asymmetry between the sequencer's execution engine and the OS's re-execution/verification logic for the exact same syscall. Any later contract call within the block that targets the affected contract address will read `class_hash` from the (now corrupted) state entry and attempt to fetch/execute the corresponding compiled class through the OS's class-loading hints. Since the class was never declared, this lookup should fail — but because the OS wrote the bogus state unconditionally, this manifests as either an OS execution panic/crash during re-execution (blocking block proving and creating a network liveness issue — "a network unable to confirm new transactions") or, if a matching (but different-block) class definition exists in the loaded set, incorrect state being baked into the committed state root, causing a divergence between the sequencer's blockifier state and the OS's committed state.

### Likelihood Explanation
`replace_class` is a standard, permissionless Cairo syscall callable by any contract from a single, ordinary transaction — no special privileges, proposer/operator role, or timing assumptions are required. Any account or contract can invoke `replace_class_syscall` with an arbitrary felt as the class hash. The only reason this hasn't caused visible failures already is that blockifier itself rejects the invalid case before the OS ever needs to process it — but the OS's job is to independently re-verify state transitions, and this asymmetry represents a real correctness gap in that verification path.

### Recommendation
Add an explicit "is declared" check in `execute_replace_class` in `syscall_impls.cairo`, mirroring the blockifier's `get_compiled_class`/declared-check logic, before constructing and committing `new_state_entry`. This resolves the TODO and removes the asymmetry between blockifier execution and OS re-execution.

### Proof of Concept
1. Deploy a contract that calls `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared on-chain (this is the same primitive used by the `test_replace_class` feature contract: [3](#0-2) ).
2. In blockifier, this transaction reverts/fails because `get_compiled_class` returns `StateError::UndeclaredClassHash`.
3. If the same syscall trace is fed into (or independently produced by) the OS's `execute_replace_class` (`syscall_impls.cairo` lines 881–920), the OS unconditionally writes the undeclared class hash into `contract_state_changes` with no validation, since the `TODO` at line 902 confirms no check is performed.
4. Any subsequent call in the same block to the affected contract address then attempts to load a compiled class for the undeclared hash, causing an OS panic/failure (halting block proving) or, in scenarios where SNOS state diverges from blockifier's rejected transaction, an inconsistency between the committed root produced by the sequencer and what the OS would compute.

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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
