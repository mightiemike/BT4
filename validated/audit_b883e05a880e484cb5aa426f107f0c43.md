### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check that the Blockifier enforces - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS Cairo implementation of the `replace_class` syscall (`execute_replace_class`) unconditionally writes an arbitrary `class_hash` supplied in the syscall request into a contract's state entry, with no verification that the class hash was actually declared. In contrast, the Blockifier's Rust implementation of the same syscall explicitly checks that the class exists before allowing the replacement. This mirrors the reported bug class: a state-mutating operation that is supposed to be gated by a precondition (there, `onlyPoolManager`; here, "class must be declared") but the check is missing in one code path.

### Finding Description
The Blockifier's `replace_class` syscall handler enforces the class-declared precondition: [1](#0-0) 
```
fn replace_class(...) -> DeprecatedSyscallResult<ReplaceClassResponse> {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler.state.set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;
    Ok(ReplaceClassResponse {})
}
```
If the referenced class hash was never declared, `get_compiled_class` fails and the syscall (and typically the whole transaction) reverts — the contract's class hash is never mutated.

The Starknet OS's Cairo re-execution of the identical syscall has no equivalent check. The relevant TODO even documents the omission: [2](#0-1) 
```
// Replaces the class.
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
```
The same missing check exists in the deprecated (Cairo0) syscall path used for old contract classes: [3](#0-2) 
```
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    ...
    let class_hash = syscall_ptr.class_hash;
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(...);
    ...
}
```
Both are reached from `execute_syscalls`/`execute_deprecated_syscalls` dispatch on `REPLACE_CLASS_SELECTOR`, which is directly triggerable by any contract executing the `replace_class` syscall as part of ordinary transaction execution — i.e., reachable from a single unprivileged transaction/contract call, exactly the reachability constraint required.

The OS is the Cairo program that is executed and STARK-proven to attest that a proposed block's state transition is a valid re-execution of the block's transactions (Starknet OS re-execution). Its job is to independently validate (not merely replay) that each state change is legitimate. Because the OS trusts prover-supplied hints (`%{ GetContractAddressStateEntry %}`) for the "before" state entry and then blindly overwrites `class_hash` with whatever `class_hash` is in the syscall request — without cross-checking that this class hash is present in the block's set of declared/known compiled classes — a prover/block-builder can construct a valid-looking OS execution and proof for a `replace_class` syscall targeting a class hash that was never declared in this state. The Blockifier (the canonical execution engine used by full nodes/sequencer to actually execute and validate transactions) would reject this exact transaction (revert due to `get_compiled_class` failure), but the OS accepts it and commits the resulting state to the proven root.

### Impact Explanation
This is a soundness gap between the two independent implementations of the same protocol rule (Blockifier vs. Starknet OS). Concretely:
- A transaction that would revert under normal Blockifier execution (replace_class to an undeclared class) can nonetheless be accepted by the OS's re-execution/proving path, since the check is absent there.
- This can result in a committed state root containing a contract whose `class_hash` field points to a class that was never declared/paid-for and has no corresponding compiled CASM/Sierra in `contract_class_changes`. Any subsequent call into that contract (or `get_class_hash_at`, `library_call`, etc. against it) would then be executing against — or attempting to load — a nonexistent class, which can freeze the contract permanently (denial of further execution) or produce a wrong committed root that diverges from what an honest Blockifier-driven node would compute for the same transaction set (honest-node divergence).
- Because the OS's output feeds directly into the block hash/state commitment that is proven and later verified/consumed downstream (e.g., by L1 verification or Starknet OS-based nodes), a wrong/unsound state transition being accepted as valid strikes at exactly the guarantees the rules require: "wrong committed root ... or honest-node divergence."

### Likelihood Explanation
Likelihood is high in terms of reachability: any account/contract can invoke the `replace_class` syscall with an arbitrary, never-declared `class_hash` as part of a normal transaction — no special privileges, staking, or network position needed. The only uncertainty is whether some external invariant (e.g., a separate consistency check elsewhere in the OS pipeline, such as a global check that all state-diff class hashes must appear in the block's declared-classes output) closes this gap; I did not find such a check in the reachable code, and the explicit TODO comment in the source strongly indicates the check is genuinely missing and acknowledged by the developers as an open issue.

### Recommendation
Add the equivalent "class is declared" check in both `execute_replace_class` (Cairo1/Sierra syscall path in `syscall_impls.cairo`) and the deprecated `execute_replace_class` in `deprecated_execute_syscalls.cairo`, mirroring the Blockifier's `get_compiled_class` check — e.g., verify the `class_hash` exists in `contract_class_changes` (or the OS's equivalent declared-classes structure) before performing the `dict_update` that mutates `contract_state_changes`. This restores parity between the Blockifier execution and Starknet OS re-execution/proving paths, closing the soundness gap called out in the existing TODO.

### Proof of Concept
1. Deploy/whitelist a contract `C` whose entry point invokes the `replace_class` syscall with a `class_hash` value `H` that has never been declared (no `Declare` transaction for `H` exists in state).
2. Under the Blockifier: calling this entry point causes `syscall_handler.state.get_compiled_class(H)` to fail, reverting the syscall/transaction — `class_hash_at(C)` is unchanged. (`crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:795-807`)
3. Under the Starknet OS re-execution (used to build the STARK proof of the block): `execute_replace_class` performs the `dict_update` on `contract_state_changes` for `C`, setting its class hash to `H`, with no check that `H` was declared (`crates/apollo_starknet_os_program/.../syscall_impls.cairo:881-920`, and the deprecated variant at `deprecated_execute_syscalls.cairo:307-329`).
4. If a prover/block builder crafts the block's hints so that this call is treated as non-reverted (or exploits the divergence in any equivalent way permitted by the missing check), the OS accepts and proves a state transition where `C`'s class hash is `H`, an undeclared class — a state transition an honest Blockifier node executing the same transaction would never produce, yielding a wrong committed root / honest-node divergence.

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
