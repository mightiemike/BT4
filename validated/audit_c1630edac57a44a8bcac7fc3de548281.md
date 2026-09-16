## Analysis

The reported bug class is: an unvalidated, attacker-controlled "code target" (in the original report, `_init`/`_calldata` for a `delegatecall`) is accepted and used to redirect contract execution/logic, with no check that the target is legitimate.

The Starknet analog is the `replace_class` syscall, which lets *any* contract atomically swap its own `class_hash` (its code) for another. This is directly analogous to `_init` in `diamondCut` because it changes which code executes for the contract going forward. Two independent execution engines implement it, and they diverge:

- The transaction-execution engine (`blockifier`) validates the target class hash before permitting the swap: [1](#0-0) [2](#0-1) 

- The Starknet OS Cairo program — the component that re-executes transactions to produce the proven state transition/commitment — implements `execute_replace_class` **without** verifying the class is declared, and this is explicitly flagged as unimplemented via a `TODO`: [3](#0-2) 

The deprecated (Cairo 0) OS syscall path has the same gap, with no validation at all before writing the new class hash into `contract_state_changes`: [4](#0-3) 

### Title
Starknet OS `execute_replace_class` accepts unvalidated/undeclared class hashes, unlike blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall lets a contract change its own code (`class_hash`) at will — the Starknet analog of `diamondCut`'s `_init`. `blockifier`, the sequencer's transaction execution engine, validates that the new class hash is declared and is a Cairo 1 class before allowing the switch. The Starknet OS Cairo program's own implementation of the same syscall (`execute_replace_class`, used to re-execute/prove the block and produce the committed state transition) performs neither check, as acknowledged by an explicit `TODO(Yoni, 1/1/2026)` comment left in production code.

### Finding Description
`blockifier::execution::syscalls::syscall_base::SyscallHandlerBase::replace_class` reads the compiled class via `self.state.get_compiled_class(class_hash)?` (erroring if undeclared) and additionally rejects Cairo 0 (deprecated) classes before calling `set_class_hash_at`: [1](#0-0) 

The equivalent deprecated-syscall handler performs the same declared-class check: [2](#0-1) 

In contrast, the Starknet OS's own Cairo implementation of `execute_replace_class` (which runs during OS re-execution / STARK-proof generation of the block, i.e. the code path that ultimately determines the committed state root) directly updates `contract_state_changes` with the caller-supplied `class_hash` with **no existence check and no Cairo-1-only restriction**, leaving only a comment that the check is still pending implementation: [5](#0-4) 

The deprecated OS syscall handler for the same operation similarly performs no validation whatsoever before committing the class-hash change to `contract_state_changes` and the revert log: [4](#0-3) 

This is a direct parallel to the `diamondCut()` finding: the OS component that is the actual source of truth for the proven state transition trusts an unvalidated, transaction-supplied "code pointer" (`class_hash`) and commits it to state, while the parallel execution engine (blockifier) enforces stricter validation. Any code path where the OS is driven independently from blockifier's own validated trace (e.g. Starknet OS re-execution/replay flows, or a future refactor that removes reliance on a pre-validated blockifier trace) would allow committing a class-hash replacement to an undeclared or Cairo-0 class hash directly into the Patricia-tree-committed state, something blockifier is specifically designed to prevent.

### Impact Explanation
If the OS accepts and commits a `replace_class` to an address that was never actually declared (or is a forbidden Cairo-0 class), the resulting state diff/commitment can diverge from what the transaction-execution engine (blockifier) would have produced or accepted, and any subsequent call to the affected contract that relies on `compiled_class_facts_bundle` lookups can hit an unresolved class fact, causing either (a) commitment of a wrong/invalid state root, or (b) unrecoverable Cairo VM failures during OS execution that block proof generation for the block — a sequencer that is unable to confirm/finalize new transactions. Since it directly controls what code a contract runs going forward, if reachable it also enables arbitrary code-identity hijacking of a contract, matching the "unauthorized account action" and "wrong committed root" impact classes.

### Likelihood Explanation
`replace_class` is a syscall callable by any unprivileged contract from a normal `INVOKE` transaction; no elevated privileges are needed to trigger the syscall itself. The blockifier-side check currently prevents this from being exploitable in the standard block-building pipeline (since malicious replace_class calls to undeclared classes get rejected by blockifier before ever reaching the OS as a valid state diff). The severity of actual exploitation therefore depends on whether the OS is ever run against untrusted/unvalidated inputs independent of blockifier's guardrails (e.g., in the OS re-execution / replay tooling mentioned in scope), which is plausible given the codebase explicitly separates OS execution from blockifier execution as two independently-implemented components that are expected to agree.

### Recommendation
Implement the pending `TODO(Yoni, 1/1/2026)` check in `execute_replace_class` (`syscall_impls.cairo`) to verify the target `class_hash` corresponds to a declared, Cairo-1 class before writing the new `StateEntry`, mirroring the checks already present in `blockifier`'s `SyscallHandlerBase::replace_class` and `DeprecatedSyscallExecutor::replace_class`. Apply the equivalent fix to the deprecated OS syscall handler in `deprecated_execute_syscalls.cairo`. Add cross-component tests asserting the OS and blockifier produce identical accept/reject decisions for `replace_class` given the same declared/undeclared/Cairo-0 class hash inputs.

### Proof of Concept
1. A user-deployed contract includes external logic that calls `replace_class_syscall(class_hash)` with an attacker-chosen `class_hash` that has never been declared on-chain (or is a Cairo-0 class hash).
2. In `blockifier`, `SyscallHandlerBase::replace_class` at [1](#0-0)  calls `state.get_compiled_class(class_hash)` and rejects the transaction (`test_replace_class`/`undeclared_class_hash` tests confirm this: `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`).
3. If the same operation is instead driven through the Starknet OS's Cairo implementation independently of blockifier's guardrail (`execute_replace_class` in `syscall_impls.cairo:881-920`), the class hash is written to `contract_state_changes` unconditionally — no equivalent to step 2's rejection exists, as flagged by the in-code `TODO`.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
    }
```

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
