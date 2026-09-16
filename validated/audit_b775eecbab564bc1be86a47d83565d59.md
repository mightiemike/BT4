### Title
Starknet OS `execute_replace_class` writes an undeclared class hash without validation, diverging from Blockifier's declared-class check - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall handler in the Starknet OS Cairo program writes a new `class_hash` into `contract_state_changes` without ever checking that the class hash is declared, while the equivalent Blockifier implementation used by the sequencer during actual block execution enforces this check before allowing the write.

### Finding Description
In the sequencer's Blockifier execution path, the deprecated `replace_class` syscall handler explicitly reads the compiled class first to force an "undeclared" error before mutating state: [1](#0-0) 

This mirrors the "current" syscall version, which is exercised in tests that explicitly assert an undeclared class hash produces an "is not declared" error: [2](#0-1) 

However, the Starknet OS's own Cairo implementation of the same syscall — used for OS re-execution / proof generation — has this validation explicitly marked as unimplemented (`TODO`) and unconditionally overwrites the contract's `class_hash` in `contract_state_changes`: [3](#0-2) 

The deprecated OS syscall handler (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) has the identical unguarded pattern — it directly overwrites `state_entry.class_hash` with the caller-supplied `class_hash` with no declared-class check: [4](#0-3) 

This is structurally the same bug class as the external report: one code path (Blockifier) validates a precondition (class must be currently/legitimately declared) before performing a state-overwriting operation, while a second code path that reaches the same logical operation (the OS's re-execution of the identical transaction) skips that validation and blindly commits the write. Just as the raffle's `selectWinner()` trusted stale eligibility data and overwrote a voucher slot without revalidating live state, the OS's `execute_replace_class` trusts attacker-supplied `class_hash` and overwrites the contract's class-hash state entry without revalidating that the class is declared.

### Impact Explanation
A transaction sender can invoke `replace_class` with an undeclared class hash. Blockifier (which executes the transaction inside the sequencer to build the block) will reject/revert it via `UndeclaredClassHash`, so the honest sequencer's actual state diff for that transaction never contains the class-hash write. The Starknet OS, however, when re-executing the same transaction as part of proof generation (Starknet OS re-execution, an in-scope reachable path from a normal transaction), will accept the syscall and write the undeclared class hash into `contract_state_changes`, producing a different resulting state (and hence a different computed state commitment/root) than what the sequencer actually produced and committed. This is exactly the kind of "wrong committed root or block hash" / "honest-node divergence" impact called out as in-scope: two components of the same protocol (blockifier vs. OS) disagree on whether a transaction succeeds and on the resulting state, threatening the soundness of the STARK proof relative to the actual chain state and potentially blocking proof verification or enabling an invalid state transition to be proven valid.

### Likelihood Explanation
The trigger is simple and requires no privileged capability: any account contract that legitimately calls the `replace_class` (or deprecated `replace_class`) syscall with an intentionally undeclared/bogus class hash reaches this exact code path. The `TODO(Yoni, 1/1/2026)` comment confirms the team is aware the check is currently missing, which corroborates that this is a genuine, currently-unpatched gap in the OS implementation rather than intentional behavior — directly analogous to the external report's "team fixed the missing revalidation" resolution.

### Recommendation
Add the same declared-class check the Blockifier performs before allowing `execute_replace_class` (and the deprecated variant) to mutate `contract_state_changes`: verify a compiled/declared class exists for `class_hash` (e.g., look it up in the OS's class dictionary and fail the syscall/transaction if absent) before constructing and committing the new `StateEntry`, ensuring the OS and Blockifier agree on validity and resulting state for every transaction.

### Proof of Concept
1. Deploy an account contract that invokes the `replace_class` syscall with a `class_hash` that has never been declared on-chain.
2. Submit this transaction to the sequencer: Blockifier's `replace_class` handler calls `get_compiled_class`/declared-check first, causing the transaction (or the call) to fail with `UndeclaredClassHash` / "is not declared" — see `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:800-804` and the equivalent test at `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`.
3. When the same transaction is re-executed by the Starknet OS (e.g., during proving), `execute_replace_class` in `syscall_impls.cairo:881-920` (and the deprecated equivalent in `deprecated_execute_syscalls.cairo:307-329`) performs no declared-class check and unconditionally writes the undeclared `class_hash` into `contract_state_changes`.
4. The OS's resulting state diff/commitment for this transaction differs from the sequencer's actual (reverted) state diff, producing a state root mismatch between the two components for the identical transaction.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-914)
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
