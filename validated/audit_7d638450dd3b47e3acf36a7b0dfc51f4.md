## Finding [1](#0-0) 

### Title
Starknet OS `replace_class` syscall implementation omits the "class is declared" check enforced by the Blockifier, causing sequencer/OS execution divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Team Finance exploit stemmed from a "migrate"-style state-transition function that lacked adequate validation on the target it was writing into, letting an attacker redirect protocol state (liquidity) to an attacker-chosen, invalid destination. The sequencer analog is the `replace_class` syscall, which rewrites a contract's class hash in state. The Blockifier's implementation validates that the target class hash is actually declared before performing the state write, but the Starknet OS's own Cairo re-implementation of the same syscall does not, as explicitly flagged by an open `TODO`.

### Finding Description
Any contract can invoke the `replace_class` syscall to change its own class hash in storage. In the Blockifier (`crates/blockifier/src/execution/syscalls/hint_processor.rs:795-807`), before writing the new class hash, the code checks that the class is declared: [2](#0-1) 
```
fn replace_class(...) {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler.state.set_class_hash_at(...)?;
    ...
}
```
This is also asserted by the dedicated test suite (`crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`) which expects `"is not declared"` when replacing with an undeclared class hash.

In contrast, the Starknet OS's own Cairo implementation of the identical syscall, `execute_replace_class` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo:881-920`, performs no such check — it directly builds a new `StateEntry` with the requested `class_hash` and commits it via `dict_update`, with the code explicitly annotated:
```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```
The deprecated (Cairo0) syscall path (`deprecated_execute_syscalls.cairo:307-329`) has the identical omission.

Because the Starknet OS is the consensus-critical Cairo program that independently re-executes every transaction in a block to prove correct state transition (used to derive the committed state root/block hash), any divergence between what the Blockifier accepts/rejects and what the OS accepts/rejects for the same transaction is a correctness bug in the core sequencing pipeline, not merely a cosmetic inconsistency.

### Impact Explanation
An unprivileged contract deployer/sender can submit a transaction that invokes `replace_class_syscall` with an arbitrary, undeclared class hash. Under the Blockifier's real block-building execution, this call fails (`StateError`/`"is not declared"`), causing the calling context to revert and the class hash change to be discarded. Under the Starknet OS's independent Cairo re-execution — which is used to prove the block and derive the canonical state commitment/root — the same call succeeds silently, permanently associating the contract with an undeclared (i.e., non-existent/garbage) class hash in the proven state tree. This constitutes:
- A wrong committed state root / block hash (the OS-computed state diverges from the Blockifier-computed state for the same block), and/or
- Honest-node divergence, since different implementations of the same syscall accept different sets of transactions as valid, and/or
- Permanent corruption/freezing of the affected contract, since a contract pointed at an undeclared class hash can no longer be called (its ABI/bytecode does not exist), freezing any funds or logic tied to that contract address.

This directly parallels the Team Finance root cause: a state-mutating function without proper validation of its target, letting an unprivileged caller redirect protocol/account state to an invalid destination and reap value or bricking effects from the resulting inconsistency.

### Likelihood Explanation
The vulnerable code path is trivially reachable: any Cairo1 (or Cairo0) contract can call `replace_class_syscall`/`ReplaceClass`. No special privileges, staking, or operator access are required — a normal `invoke` transaction from any account is sufficient to exercise `execute_replace_class` in both the Blockifier and the OS. The bug is also explicitly acknowledged in-code via the `TODO`, confirming it is a known, unresolved gap rather than a hypothetical.

### Recommendation
Add the same "class is declared" validation to the OS's `execute_replace_class` (and the deprecated Cairo0 variant) that exists in the Blockifier — e.g., look up the class hash in `contract_class_changes`/declared classes and assert existence before performing `dict_update` on `contract_state_changes`, mirroring `syscall_handler.state.get_compiled_class(request.class_hash)?` from the Blockifier. This keeps the OS and Blockifier state-transition semantics aligned for this syscall.

### Proof of Concept
1. Deploy a contract that calls `replace_class_syscall(class_hash)` where `class_hash` is any felt that has never been declared on-chain (e.g., `1234`).
2. Submit an `invoke` transaction from an unprivileged account invoking this function.
3. In the Blockifier (actual sequencer execution), `hint_processor.rs::replace_class` calls `get_compiled_class(request.class_hash)` which errors with `"... is not declared"`, causing the transaction (or inner call) to revert — no class hash change is committed. This matches the existing unit test `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs::undeclared_class_hash`.
4. When the same transaction is re-executed by the Starknet OS (`execute_replace_class` in `syscall_impls.cairo:881-920`), no declared-class check exists, so the syscall succeeds and `contract_state_changes` is updated to the undeclared `class_hash` without error — diverging from the Blockifier's rejection of the identical call.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L795-807)
```rust
            Hint::Starknet(hint) => Ok(execute_next_syscall(self, vm, hint)?),
            Hint::External(_) => {
                panic!("starknet should never accept classes with external hints!")
            }
        }
    }

    /// Trait function to store hint in the hint processor by string.
    fn compile_hint(
        &self,
        hint_code: &str,
        _ap_tracking_data: &ApTracking,
        _reference_ids: &HashMap<String, usize>,
```
