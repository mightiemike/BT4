I have sufficient evidence to produce the analog finding.

### Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes divergence from Blockifier, enabling wrong committed state root - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Karak report identifies a class of bug where a privileged parameter (manager/nodeImplementation) is accepted and applied without validating it against required invariants, letting an unprivileged actor corrupt protocol state. The Starknet analog is the `replace_class` syscall implementation inside the Starknet OS Cairo program, which — unlike the Rust `blockifier` implementation used to actually build blocks — omits the check that the target `class_hash` corresponds to a declared class, and additionally omits the Cairo0/Cairo1 compatibility check enforced by `blockifier`.

### Finding Description
In `blockifier`, the `replace_class` syscall handler explicitly validates two invariants before mutating a contract's stored class hash: [1](#0-0) 
It reads the compiled class (which fails if the class hash was never declared), and rejects Cairo0 (`ForbiddenClassReplacement`) targets. The deprecated syscall path enforces the same declared-class check: [2](#0-1) 

However, the Starknet OS Cairo re-execution program — which independently re-derives the state transition that is committed to and proven for a block — implements `execute_replace_class` without either check, and explicitly flags this gap with a TODO: [3](#0-2) 
The comment at line 902, `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`, confirms the check is knowingly missing. The OS unconditionally overwrites the contract's `class_hash` field in `contract_state_changes` with whatever `class_hash` value is provided in the syscall request, with no validation against the declared-class table and no Cairo0/Cairo1 compatibility check.

### Impact Explanation
`blockifier` is the execution engine that actually produces block state diffs; it will fail (revert) any `replace_class` call whose target class is undeclared or is a Cairo0 class. The Starknet OS program is the component that re-executes the same block to produce the proof that is ultimately verified and whose resulting state root/block hash is committed. Because the OS omits the equivalent guards, an unprivileged contract (any contract can invoke this syscall on itself, e.g., an account or arbitrary deployed contract implementing an "upgrade" entry point) that calls `replace_class` with an undeclared or Cairo0 class hash will be handled differently by the two components:
- `blockifier`: the call errors out (`StateError` / `ForbiddenClassReplacement`) and the class-hash state change is never applied.
- Starknet OS: the call succeeds unconditionally, applying the class-hash overwrite to `contract_state_changes`, which feeds directly into the committed state (Patricia tree) update and block hash.

This is a concrete "honest-node divergence / wrong committed root" scenario: the OS's computed state commitment for the block can diverge from what the actual, honestly-executed block (via `blockifier`) produced, undermining the soundness guarantee that the proof (and the block hash it attests to) faithfully represents the execution that `blockifier` performed. This is reachable purely from a single unprivileged transaction/contract call — no malicious operator, proposer, or peer is required.

### Likelihood Explanation
Any contract can invoke the `replace_class` syscall on itself with attacker-chosen `class_hash` input as long as its own logic permits the call (many upgradeable-account/contract patterns expose an "upgrade" function that calls this syscall with a caller-supplied or otherwise unvalidated hash). Supplying an undeclared or Cairo0 class hash is trivial and requires no special privileges, making the divergence condition easily triggerable by a normal user.

### Recommendation
Add the missing checks in `execute_replace_class` (crates/apollo_starknet_os_program/.../syscall_impls.cairo) mirroring `blockifier`'s `syscall_base.rs::replace_class`: (1) verify a compiled class exists for `class_hash` (i.e., that it is present in the declared-class commitment/table reachable from OS state), and (2) reject Cairo0 class hashes, matching the `ForbiddenClassReplacement` behavior, so that the OS and `blockifier` enforce identical invariants and cannot diverge on the resulting state commitment.

### Proof of Concept
1. Deploy an account/contract exposing a function that calls `replace_class_syscall(class_hash)` with an attacker-supplied `class_hash` that has never been declared (or is a declared Cairo0 class).
2. Invoke that function from a normal, unprivileged transaction.
3. In `blockifier` execution (used to build/execute the block), the call fails: `get_compiled_class` errors for the undeclared hash, or `ForbiddenClassReplacement` is returned for a Cairo0 hash — the contract's class hash is not updated.
4. In the Starknet OS re-execution (`execute_replace_class` in `syscall_impls.cairo`), the same call succeeds unconditionally: `contract_state_changes` is updated with the new (unvalidated) `class_hash` via `dict_update`, producing a state diff/commitment that does not match the one `blockifier` actually computed for the block.

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
