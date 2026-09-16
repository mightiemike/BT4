### Title
Starknet OS `replace_class` syscall omits the "class must be declared" check enforced by blockifier, allowing OS/sequencer state-root divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The BrincFi post-mortem describes a backdoor that let a privileged actor swap a contract's implementation (`upgrade_to`) and drain funds — the root problem being that the "class replacement" operation was performed without adequate validation of what it was pointing to. The Starknet analog is not an owner-key issue (Starknet has no privileged admin in the sequencer), but a structural analog in how `replace_class` (Cairo's built-in class-swap syscall, callable by any contract for itself) is validated. The Rust `blockifier` execution engine enforces that the target `class_hash` is actually declared before allowing the swap, but the Cairo Starknet OS program that re-executes the same transactions for proving purposes explicitly skips that check (marked with an unresolved `TODO`).

### Finding Description
`replace_class_syscall` is a public Starknet syscall any contract can invoke on itself from a normal transaction (analogous to the malicious `upgrade_to`/backdoor pattern in the report, just without any owner gating — that's by design on Starknet). What matters is *what* is validated before the class hash is written to state.

In the Rust blockifier (used for actual block execution and consensus/state-diff production), `replace_class` explicitly ensures the target class is declared before mutating state: [1](#0-0) 

The equivalent operation in the Cairo Starknet OS program — the code that re-executes the block to produce the STARK proof attesting to the state transition — performs the class-hash overwrite unconditionally, with an explicit acknowledgment that the "declared class" check is missing: [2](#0-1) 

The same missing check exists in the deprecated (Cairo0) syscall execution path used by the OS: [3](#0-2) 

Because the syscall dispatcher routes `REPLACE_CLASS_SELECTOR` directly into this unchecked function during OS re-execution: [4](#0-3) 

the OS accepts and commits a class-hash replacement to an undeclared class hash, while `blockifier` — which actually computes the canonical state diff that gets committed on L1 — would raise `UndeclaredClassHash` and fail/skip that state mutation for the identical call. Both engines are supposed to compute the exact same state transition for a given block of transactions; a call that mutates state in one but not the other is a soundness-breaking divergence.

### Impact Explanation
This is a "wrong committed root / honest-node divergence" class bug: for a transaction that calls `replace_class(undeclared_class_hash)`, the block-producing sequencer (blockifier) would reject the state mutation (the call errors out as declared-class-check fails), while the Starknet OS re-execution — which is trusted to prove that the committed state root corresponds to correct execution of the block — would accept and apply the class-hash change unconditionally. If the OS's computed final state (including this extra, invalid class-hash swap) is what gets attested to/proved, this creates a discrepancy between the actual sequencer-committed state and the OS-proved state, undermining the integrity guarantee that the OS proof provides over sequencer execution. This maps to "wrong committed root or block hash" / "honest-node divergence" impact categories explicitly in scope.

### Likelihood Explanation
Reachability is trivial: any account contract or arbitrary user contract can call the `replace_class` syscall on itself as part of a normal, unprivileged `INVOKE` transaction, passing an arbitrary (undeclared) `class_hash` felt as argument — no special permissions, declared class ownership, or admin key required, unlike the report's compromised admin key scenario. The only remaining question is whether some other, out-of-view guard (e.g., a later commitment-tree/consistency check in the OS, not found in the reviewed code) catches this before it affects the committed root; the reviewed code path shows no such check along the direct `replace_class` execution flow, and the explicit unresolved `TODO(Yoni, 1/1/2026)` in the source confirms the maintainers are aware the validation is currently absent.

### Recommendation
Add the equivalent "class must be declared" check to the Cairo Starknet OS's `execute_replace_class` implementations (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), mirroring the `get_compiled_class`/declared-class check performed in `blockifier`'s `replace_class` handler, before this code reaches production/mainnet-affecting proving.

### Proof of Concept
1. Deploy a contract (Cairo1) whose entry point calls `replace_class_syscall(class_hash)` with a `class_hash` value that has never been declared on-chain, and submit it via a normal `INVOKE` transaction (this mirrors the test setup already present in the repo confirming blockifier rejects such a call): [5](#0-4) 
2. Under `blockifier`, this call fails with "is not declared" (`UndeclaredClassHash`), and no class-hash state change occurs for the contract.
3. Under the Starknet OS Cairo re-execution of the same transaction, `execute_replace_class` performs the `dict_update` unconditionally, writing the new (undeclared) class hash into `contract_state_changes` regardless of whether the class was ever declared: [6](#0-5) 
4. This constitutes a concrete state-transition divergence between the two supposedly-equivalent execution engines for the identical transaction and inputs.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-920)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L676-688)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(
            contract_address=execution_context.execution_info.contract_address,
            syscall_ptr=cast(syscall_ptr, ReplaceClass*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - ReplaceClass.SIZE,
            syscall_ptr=syscall_ptr + ReplaceClass.SIZE,
        );
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-29)
```rust
#[cfg_attr(feature = "cairo_native", test_case(RunnableCairo1::Native; "Native"))]
#[test_case(RunnableCairo1::Casm; "VM")]
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
