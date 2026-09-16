### Title
Starknet OS `execute_replace_class` does not verify the target class hash is declared before committing the state change - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Cairo OS implementation of the `replace_class` syscall writes an attacker-supplied `class_hash` into a contract's state entry without verifying that this class hash corresponds to an actually declared class, unlike the equivalent Rust `blockifier` implementation which explicitly checks this via `get_compiled_class` before allowing the replacement.

### Finding Description
This is structurally analogous to the TempleDAO bug class: a privileged state-mutating operation trusts an unvalidated reference (there: an `oldStaking` contract address; here: a `class_hash`) supplied by the caller, instead of verifying it against an authoritative source before using it to mutate persistent state.

In the Rust execution layer (`blockifier`), the `replace_class` syscall base implementation explicitly validates the class hash is declared and is Cairo1 before mutating state: [1](#0-0) 

The deprecated (Cairo0) syscall handler likewise ensures the class is declared by reading it before allowing replacement: [2](#0-1) 

However, the Starknet OS Cairo program's `execute_replace_class` function — which re-executes transactions during proving to certify the state transition — takes the syscall's `class_hash` argument directly and writes it into a new `StateEntry` for the contract, with only a `TODO` comment acknowledging that the declared-class check is missing: [3](#0-2) 

Specifically, the comment at line 902 states: "TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash," and no such check (e.g., a lookup against `contract_class_changes` or a declared-classes dict) is performed anywhere else in this function before the `dict_update` call that commits the new class hash into `contract_state_changes`.

### Impact Explanation
If the OS's replacement of a contract's class hash does not enforce the "class must be declared" invariant that the actual execution layer (`blockifier`) enforces, a discrepancy exists between the two code paths responsible for producing and verifying the same state transition:
- The sequencer's `blockifier` will reject (revert) a `replace_class_syscall` call with an undeclared class hash.
- The Starknet OS, when re-executing the same transaction for proof generation (and used to certify block validity / build the OS output / commitment), would accept it and commit a class hash that was never declared, producing a `contract_state_changes` entry and thus contributing to a state commitment that a fully-conformant verifier or independent implementation would reject.

This is a state-commitment / honest-node-divergence class of bug: an inconsistency between the block-production execution semantics and the OS's re-execution semantics for a syscall reachable directly from any Cairo1 contract call (`replace_class_syscall`), which can plausibly lead to a wrong committed root/class-hash state or a block accepted by the OS/prover that diverges from `blockifier`-only nodes.

### Likelihood Explanation
`replace_class_syscall` is directly reachable by any contract as part of a single, unprivileged transaction's `__execute__` (as shown in test contracts using `syscalls::replace_class_syscall`), so triggering the OS code path requires no special privilege — just calling replace_class with an arbitrary/undeclared class hash. The missing check is explicit (a TODO left in code) rather than speculative, increasing confidence this is a genuine gap rather than a false positive from misreading logic. However, I was not able to fully confirm (due to iteration limits) whether some other upstream validation (e.g., during Sierra-to-CASM compilation tracking, hint-side declared-classes dictionary the hint `%{ GetContractAddressStateEntry %}` might reference, or a separate high-level check in `execute_syscalls.cairo`/`transaction_impls.cairo`) exists to cover this gap; those files were not fully read due to running out of tool calls. The explicit developer TODO comment strongly suggests such a check is currently absent.

### Recommendation
Add an explicit check in `execute_replace_class` (mirroring `blockifier`'s `syscall_base::replace_class`) that verifies the given `class_hash` is present among declared classes (e.g., via `contract_class_changes` dict lookup or an equivalent declared-classes membership hint) before writing the new `StateEntry`, and reject/panic execution (matching the revert behavior in `blockifier`) if the class is not declared. This should also enforce parity with the Cairo1-only restriction (rejecting Cairo0 replacement class hashes) enforced in `syscall_base.rs`.

### Proof of Concept
Not independently confirmed due to tool/iteration limits — full validation would require tracing whether `execute_replace_class`'s only guard is the TODO-noted missing check, by reading the complete call chain (`execute_syscalls.cairo`, `transaction_impls.cairo`, and any hint implementation backing `GetContractAddressStateEntry`) to rule out an equivalent check elsewhere in the OS pipeline. A Devin session with full repository access could construct a concrete PoC by crafting an OS re-execution test invoking `replace_class_syscall` with an undeclared class hash and confirming the OS accepts it while `blockifier` reverts.

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
