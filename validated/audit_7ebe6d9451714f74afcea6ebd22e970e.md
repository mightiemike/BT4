Confirmed: both the modern (`execute_replace_class` in `syscall_impls.cairo`) and deprecated (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) syscall implementations in the Starknet OS Cairo code unconditionally accept the caller-supplied `class_hash` and write it into the contract's state entry — with no check that a contract class with that hash was ever declared. This contrasts with the blockifier's native Rust implementation, which explicitly enforces this check.

### Title
Starknet OS `replace_class` syscall trusts the caller-supplied class hash without verifying it is declared, diverging from Blockifier's enforcement - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall lets a contract change its own class hash. In the Blockifier (the Rust execution engine used by the sequencer to build and execute blocks), this syscall handler explicitly reads the class to confirm it is declared before the state change is allowed: `syscall_handler.state.get_compiled_class(request.class_hash)?;` before `set_class_hash_at(...)` [1](#0-0) . However, the Cairo implementation of the same syscall inside the Starknet OS — used to re-execute and prove the block's state transition — performs no such validation. It reads the request's `class_hash`, fetches the current state entry via the `GetContractAddressStateEntry` hint, and unconditionally writes a new state entry with the caller-supplied class hash, explicitly flagged by a TODO acknowledging the gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [2](#0-1) . The same missing check exists in the deprecated syscall path [3](#0-2) .

### Finding Description
The vulnerability class mirrors the external report: an implementation trusts a value handed back by an untrusted party (there, the "me" identity URL from the auth server; here, the `class_hash` field supplied via the `replace_class` syscall request) without independently verifying it against the expected/authoritative source (there, matching domains; here, checking the class was actually declared). The Blockifier is the authoritative execution path used when the sequencer builds and executes a block; it rejects `replace_class` calls targeting an undeclared class hash by returning an error from `get_compiled_class`, causing the calling transaction to fail/revert unless the error is caught by the account's own logic. The Starknet OS is a separate Cairo re-implementation of the same execution semantics, used to independently re-derive and prove the state transition for STARK proving/verification of the block. Because the OS's `execute_replace_class` skips the "is declared" check that Blockifier enforces, a transaction that legitimately reverts under Blockifier (because it attempted `replace_class` on an undeclared class) can execute differently — or succeed further — under the OS's Cairo execution, since the syscall simply always succeeds and mutates `contract_state_changes` with the attacker-chosen hash. This creates two distinct authoritative execution engines that can disagree on the outcome of the exact same transaction.

### Impact Explanation
This is an execution-semantics divergence between the Blockifier (sequencer) and the Starknet OS (prover). Any account or contract, when invoked, can call `replace_class_syscall(some_never_declared_class_hash)` unrestricted by any privilege — every deployed account has this syscall available [4](#0-3) . Since the OS is the component whose Cairo execution trace is what actually gets proven and turned into the committed state root / block hash on L1, a mismatch between what Blockifier decided (revert) and what the OS Cairo code computes (success, writing an arbitrary/undeclared class hash into `contract_state_changes`) can produce a wrong committed root, an unprovable block, or state corruption of the targeted contract (its class hash silently set to a hash for which no class exists, or one that could later be maliciously declared by the attacker to match). This falls squarely in the accepted impact categories: honest-node divergence and wrong committed state root, reachable purely by an ordinary unprivileged transaction sender invoking a standard syscall.

### Likelihood Explanation
The `replace_class` syscall is a normal, permissionless, unprivileged syscall exposed to any Cairo contract; no operator/prover/node privilege is required to trigger it, only the ability to submit an ordinary invoke transaction whose execution reaches this syscall with an undeclared class hash argument. The TODO comment left directly in the affected code confirms this gap is known but unresolved as of the current snapshot.

### Recommendation
Add the same "class is declared" verification in both `execute_replace_class` implementations in the Starknet OS (the modern path in `syscall_impls.cairo` and the deprecated path in `deprecated_execute_syscalls.cairo`) that Blockifier already performs — i.e., verify the class hash exists in the declared/compiled class mapping (consistent with how `deploy_contract` already validates class existence, per the equivalent checks in `crates/apollo_starknet_os_program/.../execution/deploy_contract.cairo`) before permitting the state entry mutation, ensuring OS execution semantics exactly match Blockifier's rejection behavior.

### Proof of Concept
1. An attacker deploys or controls any contract (e.g., any account or non-account contract) that is entitled to call `replace_class_syscall`.
2. From an ordinary invoke transaction, the contract calls `replace_class_syscall(class_hash)` where `class_hash` corresponds to a class that has never been declared on-chain (any arbitrary/unused felt value works) — as exercised by the existing negative test `undeclared_class_hash` in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs` [5](#0-4) , which shows Blockifier's Rust path rejects this with `"is not declared"`.
3. When the block containing this transaction is re-executed by the Starknet OS for proving, `execute_replace_class` in `syscall_impls.cairo` performs no equivalent declared-class check and unconditionally commits the state mutation, diverging from the Blockifier's rejection of the same call.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L795-806)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-908)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-320)
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
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L513-516)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
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
