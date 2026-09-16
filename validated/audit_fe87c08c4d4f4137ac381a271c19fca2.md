## Title
Starknet OS `replace_class` syscall implementation omits declared-class validation performed by the Blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Blockifier (Rust) enforces that a contract can only be assigned a new class hash via the `replace_class` syscall if that class hash is (a) already declared and (b) a Cairo 1 class. The Cairo implementation of the same syscall inside the Starknet OS program — which is used to re-execute/verify blocks for proving — omits this check entirely, as acknowledged by an explicit `TODO` in the code. This creates a divergence between what the Blockifier will accept when producing a block and what the OS will accept when verifying/proving it, mirroring the reported bug class where an input-validation check present in one code path (initial validation) is missing on an alternate path that mutates the same protected state (extension).

### Finding Description
In the Blockifier, `replace_class` reads the target class via `self.state.get_compiled_class(class_hash)?` (which returns `StateError::UndeclaredClassHash` if the class was never declared) and rejects Cairo 0 classes: [1](#0-0) 

The deprecated (Cairo 0) syscall handler enforces the same declared-class requirement: [2](#0-1) 

However, the Starknet OS Cairo implementation of `execute_replace_class` (used during OS re-execution/proving, for both the Cairo1/native execution path and the deprecated Cairo0 path) directly updates the contract's class hash in `contract_state_changes` without ever checking that `class_hash` corresponds to a declared class: [3](#0-2) 

The missing check is explicitly flagged by the developers themselves: [4](#0-3) 

The same omission exists in the deprecated (Cairo0) OS syscall path: [5](#0-4) 

This is analogous to the referenced report: a validation rule ("token/class must still be valid/declared") is correctly enforced on one code path (Blockifier's live execution, analogous to "pledge creation") but is missing on a related path that also mutates the same protected state (`execute_replace_class` in the OS, analogous to "pledge extension"). Since the OS's execution trace is driven by hints (`%{ GetContractAddressStateEntry %}`) supplied by the prover rather than independently re-derived and constrained in-circuit, the absence of an in-circuit declared-class assertion means the constraint is not soundly enforced by the STARK proof.

### Impact Explanation
The Starknet OS is the component whose Cairo execution trace is proven and whose output (state diff / new state root) becomes the network's committed state. Because the OS does not assert that the `class_hash` passed to `replace_class` is declared, a prover (or a maliciously/incorrectly generated execution trace) could produce a valid-looking proof for a block in which a contract's class hash is set to an undeclared/arbitrary value. Because the check exists in the Blockifier's Rust execution engine, an honest full node running the Blockifier would refuse to reach this state, causing a divergence between what the Blockifier considers valid and what the OS is capable of proving/accepting — potentially resulting in a wrongly committed state root or class-hash-to-contract binding that bypasses the declare-a-class-before-use invariant entirely. This affects state commitment integrity, which is called out explicitly as an acceptable impact category (wrong committed root, honest-node divergence).

### Likelihood Explanation
The vulnerable code is reached by any account contract invoking the `replace_class` syscall (a standard, unprivileged operation any deployed contract can call), so the path is trivially reachable from a single submitted transaction. The gap is only exploitable by an entity that controls or can influence the OS execution trace/hints (e.g., a prover), which somewhat limits practical exploitation to that trust boundary, but the missing constraint is a genuine soundness gap in the OS program rather than merely a mempool/gateway-level omission, and the developers' own `TODO` confirms the check is currently absent.

### Recommendation
Add an explicit assertion in `execute_replace_class` (and its deprecated counterpart) that the `class_hash` corresponds to a declared, Cairo 1 (for the non-deprecated path) contract class before updating `contract_state_changes`, mirroring the Blockifier's `get_compiled_class` / `is_cairo1` checks in `syscall_base.rs::replace_class` and `deprecated_syscalls/hint_processor.rs::replace_class`. This should use an in-circuit lookup/assert against the declared-classes structure rather than relying solely on a Python hint.

### Proof of Concept
1. A prover constructs an OS execution trace containing a call that invokes `replace_class` with a `class_hash` that was never declared in the state (or that corresponds to a Cairo 0 class in the Cairo1 execution context).
2. `execute_replace_class` in `syscall_impls.cairo` (lines 881-920) directly writes `new_state_entry` with the unvalidated `class_hash` into `contract_state_changes`, with no assertion tying it to a `CompiledClassFact`/declared-classes structure.
3. The OS produces a proof for a state transition that the Blockifier itself would have rejected with `StateError::UndeclaredClassHash` (per `syscall_base.rs` lines 369-378), since the corresponding validation is absent in the Cairo/OS implementation.

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
