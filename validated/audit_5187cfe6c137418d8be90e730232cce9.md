### Title
Starknet OS `execute_replace_class` accepts undeclared class hashes, diverging from Blockifier's enforced check - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall handler in the Cairo Starknet OS program does not verify that the target `class_hash` corresponds to a declared class before writing it into contract state, whereas the equivalent Rust `blockifier` implementation explicitly enforces this check (and additionally requires the class to be Cairo 1). This mirrors the reported bug class of "missing account/resource validity check before a privileged state-mutating operation."

### Finding Description
In the Rust execution engine, `replace_class` is guarded: [1](#0-0) 

This calls `state.get_compiled_class(class_hash)` (which fails if the class was never declared) and additionally rejects non-Cairo1 classes before allowing `set_class_hash_at`.

The corresponding Starknet OS Cairo implementation, however, performs no such validation — it directly mutates the contract's state entry with the caller-supplied `class_hash`, leaving a `TODO` acknowledging the missing check: [2](#0-1) 

The deprecated (Cairo 0) syscall path in the OS has the identical gap, with no declaration check at all (not even a `TODO`): [3](#0-2) 

The OS's post-execution validation of contract classes (`guess_compiled_class_facts` / `validate_compiled_class_facts`) only validates that the hashes of guessed class facts match their contents — it does not cross-check that every `class_hash` written into a contract's state (via `replace_class` or otherwise) is backed by one of those declared/validated facts: [4](#0-3) 

Because the Starknet OS is the canonical, independently re-executed program used to produce the STARK proof attesting to a block's state transition, any divergence between what Blockifier (the sequencer's execution engine building the block) accepts/rejects and what the OS accepts/rejects during re-execution is a correctness bug: the OS is supposed to be a faithful re-implementation of the same state-transition rules that the sequencer enforces.

### Impact Explanation
If the OS's checks are weaker than Blockifier's for the same syscall, a contract could exercise `replace_class` with a `class_hash` that is not part of any declared/validated class in the OS's view (or a Cairo 0 class hash), and the OS would silently accept it and write the state entry. Since the OS is what ultimately computes/attests the state root and is used in the re-execution/proving pipeline (Starknet OS re-execution), this can produce:
- A committed contract class hash for which no corresponding compiled class content was validated by the OS, breaking the invariant that all class hashes in the committed state tree map to declared classes.
- Divergence between the state Blockifier computed while building/sequencing the block and the state the OS independently re-derives during proof generation, i.e., an "honest-node divergence" / wrong committed root scenario as covered by the validation rules.

This satisfies the Medium/High bar because it is reachable by any account contract simply invoking the `replace_class` syscall (no special privilege required), and it undermines the class-hash validity invariant on the OS/proving side of the pipeline.

### Likelihood Explanation
Likelihood is moderate: reaching `replace_class` from a transaction requires only that a contract call the syscall, which is trivially reachable by an unprivileged transaction sender. However, exploitation requires demonstrating that the OS's execution/validation path is actually invoked with a felt `class_hash` that Blockifier would have rejected, and that this leads to a persisted divergence that is not later caught elsewhere in the OS pipeline (e.g., by state-diff validation, output serialization, or Merkle-tree/class-hash consistency checks not covered by my search). The `TODO(Yoni, 1/1/2026)` comment directly acknowledges this gap is a known, currently-unaddressed issue, increasing confidence that no other check currently exists to catch it in the Cairo OS code I reviewed.

### Recommendation
Add the missing class-declaration (and, to match Blockifier's semantics, Cairo1-only) check to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, verifying against the `compiled_class_facts` bundle (or an equivalent declared-classes dictionary) before writing the new `class_hash` into `contract_state_changes`, mirroring the check already present in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class`.

### Proof of Concept
Not independently executable from static analysis alone — a concrete PoC would require running the Starknet OS program (e.g., via `starknet_os`/`starknet_transaction_prover` test harness) with a transaction that calls `replace_class_syscall` with a `class_hash` that has no corresponding declared class (or a Cairo 0 hash), and observing that the Cairo OS execution accepts it (writes the new class hash into `contract_state_changes`) while the Rust `blockifier` path for the identical transaction returns an error (`"is not declared"` / `"Cannot replace V1 class hash with V0 class hash"`, as seen in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`). I was unable to execute this within the current read-only environment; a Devin session with build/test tooling would be needed to confirm the divergence empirically end-to-end (including whether any later OS-side validation catches it).

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L53-60)
```text
// Guesses the contract classes from the 'os_input' hint variable without validating their hashes.
// Returns CompiledClassFact list that maps a hash to a CompiledClass, and the builtin costs list
// which is appended to every contract.
//
// Note: `validate_compiled_class_facts` must be called eventually to complete the validation.
func guess_compiled_class_facts{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}() -> (
    n_compiled_class_facts: felt, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
```
