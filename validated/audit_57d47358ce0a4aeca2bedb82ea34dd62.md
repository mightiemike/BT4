### Title
Starknet OS `execute_replace_class` never validates that the target class is declared (or Cairo1), diverging from blockifier's enforced check - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall unconditionally updates a contract's class hash without checking that the target class hash is declared, and (for the Cairo1 execution path) without checking that the replacement class is Cairo1 rather than a deprecated Cairo0 class. The blockifier (the actual sequencer execution engine that builds and validates blocks) enforces both checks and reverts transactions that violate them. This creates a divergence between the state transition that blockifier actually commits and the state transition the OS independently re-derives when producing/verifying the block's state root.

### Finding Description
In blockifier, `replace_class` is guarded in two places:
- The Cairo1 syscall handler `SyscallHandlerBase::replace_class` reads the compiled class (failing if undeclared) and rejects Cairo0 replacements with `ForbiddenClassReplacement`. [1](#0-0) 
- The deprecated (Cairo0) syscall handler also ensures the class is declared before applying the update. [2](#0-1) 

The `ForbiddenClassReplacement` error type exists specifically to prevent replacing with a V0 class hash. [3](#0-2) 

However, the Starknet OS's Cairo re-implementation of this syscall — used during OS re-execution to independently recompute the state diff and commit the state root — performs neither check. The Cairo1 path has an explicit TODO acknowledging the missing declared-class check, and unconditionally performs the `dict_update` on `contract_state_changes`: [4](#0-3) 

The deprecated (Cairo0) OS path exhibits the same gap — no declared-class check before updating state: [5](#0-4) 

Because the OS supports reverting this syscall (it appends a `RevertLogEntry` to unwind the class-hash change on revert), the intent is clearly that `execute_replace_class` can fail and be rolled back like in blockifier — but no failure condition is actually implemented for the undeclared-class or wrong-class-type cases.

### Impact Explanation
If a transaction invokes `replace_class_syscall` with an undeclared class hash (or, for Cairo1 callers, a deprecated Cairo0 class hash), blockifier will fail/revert that call. Depending on how the calling contract handles the failure (e.g., propagating a revert vs. swallowing it via a safe/try dispatcher), the transaction as executed and committed by the sequencer may leave the contract's class hash unchanged. When the Starknet OS independently re-executes the same block to recompute the committed state root, its lenient `execute_replace_class` will unconditionally accept the same call and mutate `contract_state_changes`, producing a different final class hash for that contract than what blockifier actually committed. This is a wrong-committed-root / honest-node-divergence class bug: the OS-computed state commitment (which underlies the L1-verified state root) can disagree with the sequencer's canonical state, undermining the correctness guarantee that OS re-execution is supposed to provide.

### Likelihood Explanation
This is reachable by any unprivileged contract deployer/caller: any Cairo1 (or Cairo0) contract can call `replace_class_syscall` (or the deprecated equivalent) with an arbitrary, attacker-chosen class hash, including one that was never declared. No special privileges, staking, or operator/proposer collusion is required — a single crafted transaction executing this syscall against an undeclared or type-mismatched class hash is sufficient to trigger divergent behavior between blockifier and the OS.

### Recommendation
Implement the same validation in the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) as exists in blockifier: verify the target class hash is present in `contract_class_changes`/the declared-classes state before committing the `dict_update`, and reject Cairo0 class hashes for Cairo1-invoked `replace_class`, mirroring blockifier's `ForbiddenClassReplacement` check, converting failures into the same revert path already scaffolded via `revert_log`.

### Proof of Concept
1. Deploy a Cairo1 contract exposing an external function that calls `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared on-chain.
2. Submit an INVOKE transaction calling this function. In blockifier, `SyscallHandlerBase::replace_class` calls `self.state.get_compiled_class(class_hash)?`, which fails because the class is undeclared, causing the call (and likely the transaction) to revert; the contract's stored class hash is left unchanged and no state diff for `class_hash` is committed. [1](#0-0) 
3. When the Starknet OS re-executes the same block to derive the committed state root, its `execute_replace_class` performs no declared-class check and unconditionally applies `dict_update` with the new (never-declared) `class_hash` to `contract_state_changes`. [6](#0-5) 
4. The resulting OS-derived state commitment for the contract's class hash diverges from the state blockifier actually committed for the block, demonstrating the honest-node/state-root divergence.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L96-97)
```rust
    #[error("Cannot replace V1 class hash with V0 class hash: {class_hash}.")]
    ForbiddenClassReplacement { class_hash: ClassHash },
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
