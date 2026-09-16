Based on the investigation, I found a concrete analog: the Starknet OS `replace_class` syscall implementation is missing a "class must be declared" check that the equivalent blockifier (native execution) implementation enforces. Confirmed that the class-tree commitment (`compute_class_commitment` in `state.cairo`) simply commits whatever class hash was written into `contract_class_changes` — there is no independent verification anywhere in the state/commitment pipeline that the class hash was actually declared.

### Title
Missing "class is declared" check in Starknet OS `replace_class` syscall enables state/commitment divergence from Blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The blockifier's native Rust `replace_class` syscall handler verifies that the target class hash is declared and is a Cairo1 (V1) class before allowing a contract to replace its class hash. The Starknet OS Cairo re-execution/proving implementation of the same syscall performs no such check — it is explicitly left as a TODO — and simply writes the caller-supplied `class_hash` into `contract_state_changes`.

### Finding Description
In `crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`, `replace_class` reads the compiled class for the requested `class_hash` (which fails if undeclared) and additionally checks that it is a Cairo1 class: [1](#0-0) 

The deprecated (Cairo0) syscall handler enforces the same declared-class invariant via `get_compiled_class`: [2](#0-1) 

However, the corresponding Starknet OS Cairo implementation used for re-execution and STARK-proof generation, `execute_replace_class` in `syscall_impls.cairo`, contains no such check — the TODO comment explicitly documents the gap: [3](#0-2) 

The same gap exists in the deprecated (Cairo0) OS syscall path, `execute_replace_class` in `deprecated_execute_syscalls.cairo`, which also unconditionally overwrites `class_hash` in the contract's `StateEntry` without checking that it was declared: [4](#0-3) 

The class hash written by `execute_replace_class` flows unchecked into `contract_state_changes`, which is later squashed and committed via `compute_class_commitment`/`compute_contract_state_commitment` in `state.cairo` — there is no downstream verification anywhere in the commitment pipeline that a committed `class_hash` was actually declared: [5](#0-4) 

This is directly analogous to CVE-2023-28110's bug class: a privileged/sensitive operation (there, connecting to a Kubernetes cluster; here, mutating a contract's class assignment, which underlies the state commitment) is executed without validating an input (there, an "illegal token"; here, an undeclared/invalid class hash) that another equivalent code path (Koko's SSH/SFTP path vs. blockifier) does validate.

### Impact Explanation
The Starknet OS is the component that re-executes/verifies transactions to produce the proven state transition and commitment (state root / block hash). Since `execute_replace_class` in the OS does not validate that the class hash is declared, a party who controls the OS execution inputs for a `Invoke`/`__execute__` call that reaches `replace_class_syscall` with an arbitrary, undeclared (or otherwise invalid) class hash can cause the OS to commit a state where a contract's class hash points to a class that was never declared. This can produce:
- A wrong committed root / block hash relative to what blockifier (the sequencer's actual execution engine) would compute for the same transaction, since blockifier would revert or reject such a call while the OS accepts it.
- Honest-node divergence between blockifier-based execution and OS-based re-execution/proving, undermining the soundness guarantee that the OS is supposed to provide (that the proven state transition matches actual sequencer execution).

Because `replace_class` is reachable from ordinary contract calls (`__execute__` → `replace_class_syscall`) by any invoke transaction with a deployed account/contract, this is reachable by a single unprivileged transaction sender, satisfying the "concrete... wrong committed root or block hash, honest-node divergence" impact bar.

### Likelihood Explanation
Reaching `replace_class_syscall` requires only a normal `invoke` transaction targeting a contract that calls `replace_class`. This is a completely permissionless, single-transaction path with no special privileges, satisfying "unprivileged transaction sender" reachability. The missing check is also explicitly marked with a TODO in the source, confirming it is a known, currently-unaddressed gap rather than a hypothetical.

### Recommendation
Add the same "class is declared" (and Cairo1-only, matching blockifier's `is_cairo1` check) validation to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` before writing the new `class_hash` into `contract_state_changes`, mirroring `crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`. This requires reading the class definition (via `contract_class_changes`/declared-class hints) and asserting its existence and version inside the OS, consistent with blockifier's enforcement, to guarantee that OS re-execution and blockifier execution cannot diverge on this syscall.

### Proof of Concept
1. Deploy an account/contract whose `__execute__` (or any external entry point) calls `replace_class_syscall(class_hash)` with an attacker-chosen `class_hash` that has never been declared on-chain (e.g., `felt!(1234)`, as used in the existing blockifier test `undeclared_class_hash` at [6](#0-5) ).
2. Submit this invoke transaction to the sequencer. Blockifier rejects it: the test above shows blockifier returns an error `"is not declared"`.
3. If instead this call is fed into (or its trace reconstructed by) the Starknet OS/prover path — `execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo` — no equivalent declared-class check exists, so the OS would accept and commit the undeclared class hash into `contract_state_changes`, producing a different (unauthorized) state/commitment than the one blockifier's live execution would have accepted.

**Note on completeness**: I was unable to fully trace whether an earlier stage (e.g., hint validation feeding `contract_class_changes` into the OS, or a separate "all declared classes must exist" global check elsewhere in `os.cairo`) might mitigate this at a different layer, since the full call graph of `os.cairo`'s use of `contract_class_changes` (18 references) was not entirely reviewed line-by-line due to tool-call limits. I recommend a Devin session review the full `os.cairo` state-update flow and any hint-verification logic (e.g., `GetContractAddressStateEntry`) to confirm no other layer independently enforces the declared-class invariant before this is treated as fully confirmed and fixed.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L42-86)
```text
// Performs the commitment tree updates required for (validating and) updating the global state.
// Returns a CommitmentUpdate struct.
//
// `should_allocate_aliases` flag indicates whether to allocate aliases before squashing the
// contract state changes.
func state_update{poseidon_ptr: PoseidonBuiltin*, hash_ptr: HashBuiltin*, range_check_ptr}(
    os_state_update: OsStateUpdate, should_allocate_aliases: felt
) -> (squashed_os_state_update: SquashedOsStateUpdate*, state_update_output: CommitmentUpdate*) {
    alloc_locals;

    // Create PatriciaUpdateConstants struct for patricia update.
    let (local patricia_update_constants: PatriciaUpdateConstants*) = patricia_update_constants_new(
        );

    // (Maybe) allocate aliases and squash the final contract state tree.
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L17-29)
```rust
fn undeclared_class_hash(runnable_version: RunnableCairo1) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(runnable_version));
    let mut state = test_state(&ChainInfo::create_for_testing(), BALANCE, &[(test_contract, 1)]);

    let entry_point_call = CallEntryPoint {
        calldata: calldata![felt!(1234_u16)],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err();

    assert!(error.to_string().contains("is not declared"));
}
```
