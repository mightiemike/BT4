## Title
Starknet OS `execute_replace_class` accepts undeclared class hashes, diverging from Blockifier's validation - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
This is the same bug class as the Mango report: a privileged state-mutating operation trusts an attacker-supplied identifier (there, a Mango account address; here, a Cairo class hash passed to the `replace_class` syscall) without validating it against the set of legitimately registered/declared entities. In the Mango case the missing check let an attacker substitute arbitrary Mango accounts; here the Starknet OS's Cairo implementation of `replace_class` lets a contract substitute an arbitrary, **undeclared** class hash for its own code, because the existence check that the Rust `blockifier` performs is missing in the OS and is only marked with a `TODO`.

### Finding Description
The `replace_class` syscall lets a contract change the class (code) it runs under. In the Rust execution engine (`blockifier`), both the current and deprecated syscall handlers explicitly verify the class is declared before allowing the replacement: [1](#0-0) [2](#0-1) 

In contrast, the Starknet OS's Cairo implementation of the same syscall (`execute_replace_class` in `syscall_impls.cairo`, used for the Cairo1/current syscall ABI during OS re-execution/proving) writes the new class hash into `contract_state_changes` unconditionally, with an explicit `TODO` acknowledging the missing check: [3](#0-2) 

The same omission exists in the deprecated-syscalls OS path (`deprecated_execute_syscalls.cairo`), which also performs no declared-class check before updating `contract_state_changes`: [4](#0-3) 

The Starknet OS is the canonical state-transition function whose execution is proven (via STARK) and whose output (state diffs, receipts, and ultimately the committed state root) is what gets accepted as the network's true post-block state. `blockifier` is used by the sequencer for fast candidate-block execution, but the OS independently re-derives the outcome of every transaction, including running syscalls like `replace_class` against its own state-change dictionary — it does not simply trust blockifier's result.

### Impact Explanation
Because the OS lacks the "class is declared" check that blockifier enforces, the two execution engines can produce different outcomes for the exact same transaction that invokes `replace_class_syscall` with an undeclared (or non-Cairo1) class hash:
- In blockifier, `get_compiled_class(class_hash)` fails, the syscall reverts, and the transaction is recorded as reverted with no class-hash state change.
- In the OS, the same call unconditionally succeeds and commits a `StateEntry` with a class hash that corresponds to no declared class, changing the storage/output computed for that transaction.

Since the OS's re-computed state changes are what is committed to via the STARK proof (and ultimately the on-chain state root), this creates an honest-node divergence: full nodes/RPC serving execution via blockifier will report one execution result and post-state for the contract, while the proven, canonical state root produced by the OS reflects a different one. Concretely, this can permanently brick the affected contract (it now points at a class hash with no compiled code, so any subsequent call to it fails under blockifier), and it can produce a wrong committed state root relative to what blockifier-based nodes compute and expect — undermining the network's ability to agree on the correct state.

### Likelihood Explanation
This path is reachable by any contract executing ordinary code during a normal transaction — a contract simply needs to invoke the standard `replace_class_syscall` with a class hash that has never been declared. No privileged role (operator, prover, sequencer) is required to trigger the divergence; it stems purely from the transaction sender's calldata/contract logic exercising a syscall available to every contract. The bug is also self-documented in the code as an explicit outstanding `TODO`, confirming the check truly is missing rather than implemented elsewhere.

### Recommendation
Add the same "class hash is declared" (and, to match blockifier's stricter Cairo1-type check for the new syscall interface) validation to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` before writing the new `StateEntry`, e.g. by reading/asserting existence of the compiled class fact for `class_hash` (mirroring `syscall_handler.state.get_compiled_class(request.class_hash)?` in `crates/blockifier/src/execution/syscalls/syscall_base.rs`), so that the OS's accepted state transitions match blockifier's exactly.

### Proof of Concept
1. An attacker deploys/controls a Cairo1 contract.
2. In an `__execute__`/external entry point, the contract calls `replace_class_syscall(class_hash)` where `class_hash` is a value that has never been the subject of a successful `Declare` transaction.
3. Under blockifier (the sequencer's real execution engine), this call fails at `self.state.get_compiled_class(class_hash)?` in `crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`, and the transaction reverts.
4. If the Starknet OS re-executes the same call sequence (e.g., during proof generation for the block, or in flows where the OS's own accounting of the syscall differs from blockifier's revert path), `execute_replace_class` in `syscall_impls.cairo:881-920` accepts the undeclared `class_hash` unconditionally and commits a new `StateEntry` for the contract, producing a state change/output that blockifier never produced for that transaction — a divergence between the two "sources of truth" for the block's state.

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
