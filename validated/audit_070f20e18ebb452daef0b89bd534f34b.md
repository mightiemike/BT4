## Title
Missing declared-class validation in Starknet OS `execute_replace_class` allows contracts to be assigned undeclared class hashes, diverging from Blockifier execution semantics - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall writes a new `class_hash` into a contract's state entry without ever verifying that the class hash refers to a declared class. This mirrors the "missing check" bug class from the report (a validation that exists in intent but is not actually enforced), except here the check is not merely a typo — it is explicitly marked as not-yet-implemented via a `TODO` comment, while the equivalent Rust Blockifier code path *does* perform this check.

### Finding Description
In `execute_replace_class` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`, the class hash from the syscall request is written directly into the contract's new `StateEntry` with no validation that a class with that hash was ever declared: [1](#0-0) 

The comment on line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms the check is not implemented, and the code proceeds straight to `dict_update` of `contract_state_changes` with the unvalidated `class_hash`.

This is in direct contrast to the Blockifier's own execution of the same syscall, which explicitly ensures the class is declared before updating state: [2](#0-1) 

and the Native/CASM-path `replace_class` implementations that ultimately route to the same declared-class enforcement via `self.base.replace_class(...)`: [3](#0-2) 

Because the Starknet OS is the component that independently re-executes transactions to compute the canonical state diff and state commitment used for proving (Starknet OS re-execution is explicitly in scope per the task), any semantic gap between it and Blockifier's execution rules is a correctness bug: the OS is supposed to reproduce exactly the same state transitions that the sequencer (Blockifier) committed to the block.

### Impact Explanation
If the OS's `execute_replace_class` accepts and commits an undeclared class hash where Blockifier would have rejected/reverted the same call (since Blockifier's `replace_class` fails via `syscall_handler.state.get_compiled_class(request.class_hash)?` when the class is undeclared), the OS can compute a different final state (and therefore a different state root / block hash commitment) than the one actually produced and finalized by the sequencer. This falls under "wrong committed root or block hash" / "honest-node divergence" — a block that was valid and finalized by the sequencer could fail proof validation, or worse, a state root could be computed that includes a contract entry pointing at a nonexistent/undeclared class, which is an invalid protocol state that downstream tooling (RPC, other OS runs, L1 verification) does not expect.

### Likelihood Explanation
This is reachable by any account contract executing `replace_class(class_hash)` with an arbitrary, undeclared `class_hash` from within its own `__execute__` (or any contract call), which is a standard unprivileged operation available to any transaction sender. No special privileges are required — a normal transaction sender only needs to deploy/register an account whose logic calls the `replace_class` syscall with a crafted, undeclared class hash.

### Recommendation
Implement the missing declared-class check in `execute_replace_class` (resolve the `TODO(Yoni, 1/1/2026)`), mirroring the Blockifier's behavior of confirming the class is declared (e.g., verifying the class hash exists in the OS's compiled-class mapping/state) before committing the `StateEntry` update, and route to the same revert-on-failure path (via `revert_log`) that other failing syscalls use.

### Proof of Concept
1. Deploy an account contract whose `__execute__` calls the `replace_class` syscall with a `class_hash` that has never been declared.
2. Under Blockifier execution, `syscall_handler.state.get_compiled_class(request.class_hash)?` fails (per `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:800-801`), causing the syscall/call to fail and the transaction's execution phase to revert (only fee/nonce updates persist).
3. When the same transaction trace is re-executed by the Starknet OS via `execute_replace_class` in `syscall_impls.cairo`, no equivalent declared-class check exists (line 902 TODO), so the contract's `StateEntry.class_hash` is unconditionally updated to the undeclared hash and merged into `contract_state_changes`, producing a state diff/commitment that does not match what Blockifier actually finalized for the block.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-917)
```text
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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L395-406)
```rust
    fn replace_class(&mut self, class_hash: Felt, remaining_gas: &mut u64) -> SyscallResult<()> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.replace_class.base_syscall_cost(),
            SyscallSelector::ReplaceClass,
        )?;

        self.base
            .replace_class(ClassHash(class_hash))
            .map_err(|err| self.handle_error(remaining_gas, err))?;
        Ok(())
    }
```
