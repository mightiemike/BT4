### Title
Starknet OS `replace_class` syscall omits class-declaration/type validation performed by Blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall unconditionally overwrites a contract's `class_hash` in the state-changes dict without verifying that the target class hash is declared or that it is a valid (Cairo1) class, unlike the Blockifier's Rust implementation which performs both checks. This is a reachable path for any contract invoked by an ordinary transaction sender that calls `replace_class`.

### Finding Description
The Blockifier's `replace_class` syscall implementation explicitly validates the class before mutating the contract's state: [1](#0-0) 

This mirrors the deprecated (Cairo0) syscall handler, which also fetches the compiled class before allowing the replacement: [2](#0-1) 

However, the Starknet OS's own Cairo re-implementation of `replace_class` (used during OS re-execution / proof generation) skips this validation entirely. It reads the class hash from the request, performs `GetContractAddressStateEntry`, and directly writes the new `class_hash` into `contract_state_changes` with no check that the class is declared, and no check that it is not a deprecated (Cairo0) class: [3](#0-2) 

Notably, the code contains an explicit acknowledgment of the gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` at line 902. [4](#0-3) 

The deprecated syscall path in the OS (used for Cairo0 execution contexts) has the identical omission: [5](#0-4) 

This is a divergence between the two independent implementations of the same protocol rule that must otherwise agree bit-for-bit: the Blockifier (used to build/validate blocks) enforces `get_compiled_class` success and Cairo1-only replacement (`ForbiddenClassReplacement` for non-Cairo1 classes), while the Starknet OS (used to re-execute transactions and produce the STARK proof of the state transition) does not enforce either check.

### Impact Explanation
Because Blockifier and the Starknet OS must independently derive the same execution result and state diff for every transaction (the OS re-executes transactions from scratch as part of proof generation and Starknet OS re-execution/validation), any code path where the OS's Cairo logic is more permissive than Blockifier's Rust logic constitutes an honest-node/implementation divergence. If a call to `replace_class` supplies a class hash that Blockifier would reject (undeclared class, or a Cairo0/deprecated class hash disallowed for replacement), Blockifier will revert that call during block building, whereas the OS's Cairo logic performing the same call independently would not detect any error and would happily set `class_hash_at[contract_address]` to the undeclared/invalid class hash. This can produce a state diff and resulting state commitment/block hash for the OS-verified block that differs from what Blockifier actually computed and committed, i.e., a wrong committed root, or the inability for the OS to produce a proof that matches the real, canonical execution result. This falls squarely in the "honest-node divergence"/"wrong committed root" impact category explicitly in scope.

### Likelihood Explanation
This is trivially reachable: any account or contract can invoke `replace_class` with an arbitrary (including undeclared) class hash as part of an ordinary transaction — no special privileges are required, matching the exact "unprotected function reachable by an unprivileged caller" bug class from the reference report. Reaching the divergence requires only that a contract triggers `replace_class` with a class hash that Blockifier's check would reject but that reaches the OS's execution logic (e.g., during OS-based re-execution / proof pipelines that do not first filter through Blockifier's stricter check, such as `starknet_os_flow_tests`, `echonet`, or direct OS test-flow harnesses). Given this is a real code path with an explicit unresolved TODO acknowledging the missing check, likelihood of encountering the divergence in any OS-driven execution flow is high; the primary uncertainty is whether current production Sequencer flows always gate all transactions through Blockifier first (which would mask the effect for the canonical chain) versus flows (fault-proof, native re-execution testing, echonet OS replay) where the OS logic runs independently and could diverge — I could not fully confirm from the index whether every current production path guarantees Blockifier pre-filters exactly this class-hash before OS invocation in all deployment topologies.

### Recommendation
Add the missing checks in the OS's Cairo `execute_replace_class` (both `syscall_impls.cairo` and the deprecated variant in `deprecated_execute_syscalls.cairo`) to mirror Blockifier's logic exactly: verify the class hash corresponds to a declared class (equivalent of `get_compiled_class`), and reject replacement with non-Cairo1 (deprecated) classes, returning the equivalent of `ForbiddenClassReplacement`/failure response instead of silently succeeding.

### Proof of Concept
1. A contract executes the `replace_class` syscall with a `class_hash` value that has never been declared (or that corresponds to a deprecated Cairo0 class).
2. In Blockifier's execution path (`crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`), `self.state.get_compiled_class(class_hash)` fails (class undeclared) or `is_cairo1` check fails, causing the syscall/transaction to fail/revert.
3. If the same call is independently re-executed through the Starknet OS's Cairo implementation (`crates/apollo_starknet_os_program/.../syscall_impls.cairo:881-920`), no equivalent check exists — the OS directly writes the requested `class_hash` into `contract_state_changes` for the contract, succeeding where Blockifier would have failed.
4. This produces different state diffs/state roots between the two independently-derived execution results for the same transaction, which is precisely the kind of divergence the "Starknet OS re-execution" scope is meant to catch. [3](#0-2) [1](#0-0)

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
