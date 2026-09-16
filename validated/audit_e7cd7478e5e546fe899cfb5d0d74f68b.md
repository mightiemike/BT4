Based on the investigation, I found a concrete root-cause bug analog reachable from a single transaction sender.

### Title
Missing declared-class validation in Starknet OS `execute_replace_class` allows honest-node state divergence vs blockifier - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The blockifier's Rust `replace_class` syscall handler validates that the target class hash is declared (and is Cairo1) before writing the new class hash, but the Cairo Starknet OS (SNOS) implementation of the same syscall has this check explicitly missing, marked with a `TODO`.

### Finding Description
In the blockifier, `SyscallHandlerBase::replace_class` reads the compiled class for the requested `class_hash` via `self.state.get_compiled_class(class_hash)?` before performing the state write, which returns `StateError::UndeclaredClassHash` if the class was never declared, and also rejects non-Cairo1 (V0) classes: [1](#0-0) 

The deprecated (Cairo0) syscall path performs the equivalent check: [2](#0-1) 

In the Cairo implementation of the Starknet OS, which re-executes transactions to build the trace that is proven and to compute the committed state root/block hash, the `execute_replace_class` function performs the state-entry update for the contract's class hash without ever checking whether `class_hash` corresponds to a declared class. This is explicitly flagged as a missing check: [3](#0-2) 

The comment on line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms the check is known to be absent in the OS at this point in time. The deprecated Cairo0 OS syscall path (`deprecated_execute_syscalls.cairo`) has the same gap. [4](#0-3) 

Also worth noting: even the "new" syscall executor for SNOS (`snos_syscall_executor.rs`) implements `replace_class` as a pure no-op that always succeeds and never touches state or validates the class hash: [5](#0-4) 

### Impact Explanation
Any account contract can call the `replace_class` syscall (via `test_replace_class`-style entry points, or any contract invoking `syscalls::replace_class_syscall`) with an arbitrary, undeclared `class_hash`. In the blockifier this transaction reverts with `StateError::UndeclaredClassHash`, so the sequencer/blockifier will not apply the state change (the entry point invocation fails, and depending on call context the transaction reverts or fails). If the Starknet OS accepts and commits the class-hash replacement regardless of whether the class is declared (per the code path shown, gated only by the TODO), the OS-computed state diff/trace for that transaction will diverge from the blockifier's rejection/revert behavior for the same transaction. Since the OS's output feeds directly into the committed state root and block hash (Patricia tree updates via `contract_state_changes` and the `revert_log`), this is a state-root/block-hash divergence between the block-building/execution engine (blockifier) and the proving/re-execution engine (Starknet OS) for the exact same block and transaction. This maps to "honest-node divergence" / "wrong committed root or block hash" — the OS could accept a state transition (writing an undeclared class hash into a contract's class slot) that the sequencer's execution engine would never have produced, breaking equivalence between execution and proof, which is the core soundness property the OS is required to preserve.

### Likelihood Explanation
This is directly reachable by any unprivileged transaction sender: a single `INVOKE` transaction to any contract exposing a call to `replace_class_syscall` (a standard native Starknet syscall available to every account/contract) with an undeclared class hash is sufficient to exercise this path. No special privileges, contract deployer/declarer role, or L1 message sender status is needed — invoking `replace_class` with a bogus class hash is a normal-looking single transaction.

### Recommendation
Add the same declared-class check present in the Rust blockifier (`self.state.get_compiled_class(class_hash)?`, i.e. verifying the class hash is declared and is a Cairo1 class) to the Cairo `execute_replace_class` implementation in `syscall_impls.cairo`, and to the corresponding deprecated syscall path in `deprecated_execute_syscalls.cairo`, before writing the new `StateEntry`. Additionally audit `snos_syscall_executor.rs`'s `replace_class` no-op implementation to ensure it is not the code path actually exercised during OS re-execution/proving, or add the equivalent validation there as well, to guarantee the OS's semantics exactly match the blockifier's for every syscall.

### Proof of Concept
1. Deploy any account/contract exposing a public entry point that calls `starknet::replace_class_syscall(class_hash)` (e.g., the existing test helper `test_replace_class`, seen at `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo:514`).
2. Submit an `INVOKE` transaction from any unprivileged account calling that entry point with a `class_hash` that has never been declared on-chain.
3. In the blockifier this fails with `StateError::UndeclaredClassHash` (as validated by the existing test `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`, `undeclared_class_hash`).
4. Trace the same transaction through the Starknet OS's Cairo `execute_replace_class` (`syscall_impls.cairo:881-920`): the missing check (per the explicit TODO at line 902) means the OS state-entry update proceeds unconditionally, producing a different state diff/state root for this transaction than the blockifier would have produced/rejected.

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
