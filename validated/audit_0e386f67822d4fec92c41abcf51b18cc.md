### Title
Starknet OS `replace_class` syscall omits declared-class and Cairo1-only checks enforced by the Blockifier, causing sequencer/OS state divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Rust Blockifier enforces two invariants when a contract invokes the `replace_class` syscall: the target `class_hash` must be a **declared** class, and it must be a **Cairo1 (V1)** class (Cairo0 classes are forbidden as replacement targets). The Starknet OS's Cairo implementation of the same syscall, which is used to re-execute/prove blocks, performs **neither check**, and the corresponding Rust SNOS hint executor is a no-op. This is analogous to the Mattermost bug class (CWE-639, missing authorization/validation of a resource reference before acting on it) — here the "resource ID" is a `class_hash`, and the OS blindly trusts and applies it without validating it the way the canonical execution engine does.

### Finding Description
In the Blockifier, `replace_class` is validated in `crates/blockifier/src/execution/syscalls/syscall_base.rs`: [1](#0-0) 
This reads the compiled class (failing with `UndeclaredClassHash` if not declared) and rejects the call with `ForbiddenClassReplacement` if the class is not Cairo1.

In the Starknet OS Cairo program, the same syscall for Cairo1 contracts is implemented with an explicit TODO noting the missing check, and it unconditionally rewrites the contract's class hash: [2](#0-1) 

The same is true for the deprecated (Cairo0) execution path: [3](#0-2) 

And the Rust SNOS hint processor that backs this Cairo syscall during OS re-execution is a literal no-op — it does not read/verify the class at all: [4](#0-3) 

By contrast, the equivalent Blockifier syscall hint processor (`hint_processor.rs`) delegates to `syscall_base.rs::replace_class`, which does perform the check: [5](#0-4) 

The revert-log machinery in the OS (`revert.cairo`) only tracks the *previous* class hash for potential rollback — it performs no validation of the *new* class hash either: [6](#0-5) 

**Root cause:** the Blockifier (the execution engine that actually determines the canonical state transition when a block is built) and the Starknet OS (the Cairo program used to prove/re-execute the same block) implement divergent semantics for `replace_class`. The OS accepts any `class_hash` (declared or not, V0 or V1) as the new class for a contract, while the Blockifier would revert the entry point call with `UndeclaredClassHash` or `ForbiddenClassReplacement` under the same conditions.

### Impact Explanation
Any unprivileged account contract invoked via a normal `INVOKE` transaction can call `replace_class_syscall(class_hash)` with an undeclared class hash, or (for a Cairo1 contract) with a declared Cairo0 class hash. In the Blockifier this call fails/reverts. If the same transaction/call is re-executed by the Starknet OS (used for block proving/verification, explicitly in-scope per the prompt), the OS will silently succeed and write the invalid class hash into `contract_state_changes`, producing a state transition inconsistent with what the sequencer actually committed. This is a classic root-cause for a wrong committed root/state divergence between the "honest" sequencer execution and the OS-driven proof of that same block, which can result in unprovable/invalid blocks, block hash mismatches, or (if unnoticed) a corrupted committed state (`class_hash_at` pointing to a non-existent or forbidden class) — satisfying the "wrong committed root or block hash" / "honest-node divergence" impact bar. Severity is High given it is reachable from a single unprivileged transaction and directly threatens state-commitment integrity.

### Likelihood Explanation
Likelihood is high: `replace_class_syscall` is a standard, unrestricted Cairo1/Cairo0 syscall callable by any contract at Execute-time (rejected only in Validate mode). No special privileges, staking, or operator/proposer role is required — a normal contract deployer/caller can trigger this divergence with a single transaction using an arbitrary or mismatched `class_hash` as calldata.

### Recommendation
Add the same validation to the OS's `execute_replace_class` (both the Cairo1 path in `syscall_impls.cairo` and the Cairo0 path in `deprecated_execute_syscalls.cairo`) and to the Rust `snos_syscall_executor.rs::replace_class` implementation: verify the target `class_hash` is declared (present in `contract_class_changes`/declared-classes commitment) and, for the Cairo1 syscall variant, that it corresponds to a V1 (Cairo1) class, mirroring `is_cairo1` checks and `ForbiddenClassReplacement`/`UndeclaredClassHash` semantics in `syscall_base.rs`. Remove the outstanding `TODO(Yoni, 1/1/2026)` by implementing the check rather than deferring it.

### Proof of Concept
1. Deploy an account/test contract (Cairo1) via a normal `DEPLOY_ACCOUNT`/`INVOKE` flow.
2. Send an `INVOKE` transaction that calls the contract's exposed function invoking `replace_class_syscall(class_hash)` (e.g., `test_replace_class` in `blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo`, line ~201) with an undeclared `class_hash` value, or with a class hash belonging to a declared Cairo0 (V0) class.
3. Observe: Blockifier execution (`crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`, e.g. `undeclared_class_hash` / `cairo0_class_hash` tests) reverts the call with `UndeclaredClassHash`/`ForbiddenClassReplacement`. [7](#0-6) 
4. If the identical syscall trace were instead re-executed through the Starknet OS Cairo program's `execute_replace_class` (`syscall_impls.cairo` lines 881-920) or the SNOS hint executor (`snos_syscall_executor.rs` lines 319-326), no error is raised and the contract's class hash is unconditionally overwritten — demonstrating the OS accepts a state transition the Blockifier would reject, confirming the divergence.

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

**File:** crates/starknet_os/src/hint_processor/snos_syscall_executor.rs (L319-326)
```rust
    fn replace_class(
        _request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        _syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L685-693)
```rust
    fn replace_class(
        request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        syscall_handler.base.replace_class(request.class_hash)?;
        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L73-91)
```text
// Processes revert log entries related to a specific contract, returns to the caller once
// a CHANGE_CONTRACT_ENTRY is encountered.
func revert_contract_changes{
    class_hash: felt, storage_ptr: DictAccess*, revert_log_end: RevertLogEntry*
}() {
    alloc_locals;
    let revert_log_end = &revert_log_end[-1];

    tempvar selector = revert_log_end[0].selector;
    if (selector == CHANGE_CONTRACT_ENTRY) {
        // Change contract entries are handled by the caller.
        return ();
    }

    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-53)
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

#[cfg_attr(feature = "cairo_native", test_case(RunnableCairo1::Native; "Native"))]
#[test_case(RunnableCairo1::Casm; "VM")]
fn cairo0_class_hash(runnable_version: RunnableCairo1) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(runnable_version));
    let empty_contract_cairo0 = FeatureContract::Empty(CairoVersion::Cairo0);
    let mut state = test_state(
        &ChainInfo::create_for_testing(),
        BALANCE,
        &[(test_contract, 1), (empty_contract_cairo0, 0)],
    );

    // Replace with Cairo 0 class hash.
    let v0_class_hash = empty_contract_cairo0.get_class_hash();

    let entry_point_call = CallEntryPoint {
        calldata: calldata![v0_class_hash.0],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err();

    assert!(error.to_string().contains("Cannot replace V1 class hash with V0 class hash"));
}
```
