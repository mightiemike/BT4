### Title
Missing "class is declared" validation in the Starknet OS `replace_class` syscall handler causes state divergence from Blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Cairo 1 `replace_class` syscall handler in the Starknet OS (`execute_replace_class`) unconditionally rewrites a contract's class hash without verifying that the target class hash is actually a declared class, unlike its exact Rust counterpart in Blockifier, which performs this check and additionally restricts the replacement to Cairo1 classes. This mirrors the Value DeFi root cause: a single missing guard (there, a missing `initialized = true`; here, a missing "class is declared" check) lets an unprivileged caller drive protected state into a configuration the two independent execution engines disagree on.

### Finding Description
Blockifier's native `replace_class` implementation explicitly validates the target class before mutating storage: [1](#0-0) 

```
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

The same guard exists in the deprecated (Cairo0) syscall path in Blockifier: [2](#0-1) 

However, the Starknet OS's Cairo implementation of the identical syscall — which is the code that is actually proven and constitutes the canonical state-transition function — performs **no such check**, as the author's own TODO comment acknowledges: [3](#0-2) 

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

The same missing check exists in the deprecated (Cairo0) syscall path of the OS: [4](#0-3) 

Blockifier is used by the sequencer to build blocks and decide which transactions succeed/revert; the Starknet OS is the ground-truth execution engine whose trace is proven and whose resulting state root is committed on L1. Any unprivileged contract calling `replace_class_syscall(class_hash)` with a class hash that is not declared (or, on Cairo1 paths, not of Cairo1 type) will be **rejected by Blockifier** (causing the call to revert) but will be **silently accepted by the Starknet OS**, since the OS unconditionally updates `contract_state_changes` with whatever `class_hash` value is supplied in the syscall request, without cross-checking `contract_class_changes` (the dict tracking declared classes) or the class's Cairo version.

### Impact Explanation
This is a state-transition-function divergence between the sequencer's Blockifier execution and the Starknet OS's canonical (proven) execution:
- A transaction that Blockifier reverts (and therefore the sequencer commits as a no-op/failed call, producing one state diff) would be accepted as successful by the OS when re-executed for proving, producing a *different* state diff (an arbitrary/garbage or wrong-typed class hash written to the caller's own storage entry).
- This causes an honest-node divergence: the state root/block hash computed and committed by the sequencer will not match what the Starknet OS proof asserts, or the discrepancy could make the block unprovable, halting the network's ability to confirm new blocks (a "network unable to confirm new transactions" condition), or could allow an attacker to force a contract's class hash into an unvalidated/undeclared value that later execution of that contract account relies on, undermining assumptions the rest of the OS makes about `contract_class_changes` consistency.

### Likelihood Explanation
This is trivially reachable by any unprivileged deployed Cairo contract by simply invoking `replace_class_syscall` with a crafted `class_hash` (undeclared or Cairo0) from any `__execute__` context — no special privileges, no dependency on a malicious operator, prover, or peer are required; it is a single-transaction call.

### Recommendation
Add the missing validation to both `execute_replace_class` implementations in the Starknet OS Cairo source (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), mirroring Blockifier's checks exactly:
1. Verify `class_hash` has an entry in `contract_class_changes` (i.e., is declared) before performing the `dict_update` on `contract_state_changes`.
2. For the Cairo1 syscall path, additionally reject Cairo0 (deprecated) class hashes, matching Blockifier's `is_cairo1` / `ForbiddenClassReplacement` restriction, to keep both execution engines byte-for-byte consistent.

### Proof of Concept
1. Deploy any contract account (no special role required).
2. From that account's `__execute__`, call `replace_class_syscall(class_hash)` where `class_hash` is a felt that was never declared in the chain (or, for parity testing, a legitimately declared Cairo0 class hash).
3. Observe that Blockifier's `replace_class` (`crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`) returns an error (`UndeclaredClassHash` / `ForbiddenClassReplacement`), so the sequencer treats the call as reverted and excludes the class-hash write from the committed state diff.
4. Feed the same transaction/syscall trace into the Starknet OS's `execute_replace_class` (`crates/apollo_starknet_os_program/.../syscall_impls.cairo:881-920`); because no declared/type check exists there, the OS accepts the syscall and writes the new (undeclared/invalid) `class_hash` into `contract_state_changes`, producing a state diff inconsistent with what Blockifier computed for the same transaction.

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
