Based on my investigation, I found a concrete, code-verified analog: the Starknet OS's `execute_replace_class` Cairo function is missing a check that the blockifier (native Rust execution) enforces, creating a divergence between what the sequencer/blockifier accepts during execution and what the OS accepts during re-execution/commitment.

### Title
Missing "class is declared" validation in OS `execute_replace_class` diverges from blockifier's `replace_class` syscall, risking honest-node state divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The blockifier's Rust implementation of the `replace_class` syscall enforces that the target `class_hash` is a declared class before permitting the state write [1](#0-0) , and even the deprecated syscall path enforces the same check via `get_compiled_class` [2](#0-1) . In contrast, the Starknet OS's Cairo implementation of `execute_replace_class` performs the state update unconditionally and explicitly documents the missing check via a `TODO`: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [3](#0-2) . The deprecated OS syscall path (`deprecated_execute_syscalls.cairo`) has the identical gap — it writes the new class hash to `contract_state_changes` with no declaration check at all [4](#0-3) .

### Finding Description
This is the same bug class as the reported `RFPSimpleStrategy#setPoolActive` issue: a state-mutating code path is missing a validation check that is present (and relied upon) in a parallel/equivalent code path, breaking the invariant that both paths agree on what is permitted. Here:
- The blockifier's `SyscallHandlerBase::replace_class` requires `self.state.get_compiled_class(class_hash)` to succeed (i.e., the class must be declared), and further requires the class to be Cairo1 [1](#0-0) . Both blockifier and native execution paths delegate to this same base implementation, so this check is authoritative for block building.
- The Starknet OS, which re-executes transactions to independently compute the state root/commitment for proof generation, does **not** perform this check — it reads whatever `class_hash` is supplied and unconditionally writes it into `contract_state_changes` via `dict_update` [5](#0-4) .

Because the sequencer's blockifier is what filters/executes transactions before they're included in a block, in the current well-formed flow this divergence is latent (only successfully-blockifier-executed txs, which already passed the declared-class check, ever reach the OS). However, this is exactly the kind of "missing guard that another code path relies on" pattern flagged in the reported issue: any future change that causes the OS to be invoked on a state/transaction not first vetted by the same blockifier check (e.g., alternate execution engines, native Cairo execution paths, or a bug in a sibling syscall handler that skips the equivalent Rust check) would allow the OS to accept a `replace_class` to an arbitrary/undeclared class hash that the canonical execution layer would have rejected, producing a state root the network computed differently from what a compliant node executing via the Rust path would compute — i.e. honest-node divergence / wrong committed root.

### Impact Explanation
If the OS accepts and commits a `replace_class` to an undeclared (or invalid) class hash that the reference blockifier implementation would reject, the resulting state entries would diverge between the OS-computed commitment and any node relying on strict blockifier-side validation. A contract whose class hash is corrupted this way becomes permanently unusable (denial of service for that contract), and more broadly, any divergence between the OS's accepted state transitions and the blockifier's accepted state transitions threatens the correctness of the state commitment/proof pipeline, which is a core network safety property.

### Likelihood Explanation
Under the current code, this is not directly triggerable by an unprivileged account because blockifier's `replace_class` gate stands in front of every real execution before the OS re-runs it. However, the same `TODO` comment and its persistence in production code indicate the OS-side authors are aware the check is missing and consider it a defect to fix, not a deliberate design choice — the existence of the deprecated OS handler with the identical omission (no comment, no check at all) increases the risk that some execution surface bypasses the Rust-level check. This is a "missing check known to the authors as needed" defect analogous in kind to the reported `setPoolActive` issue, and it should be classified medium/high pending a concrete confirmation that a bypass path exists via other execution engines (e.g. Cairo Native) that don't funnel through `syscall_base.rs::replace_class`.

### Recommendation
Add the same "class hash must correspond to an actually declared class" check in `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring the blockifier's `get_compiled_class`/declared check in `syscall_base.rs`, so the OS's re-execution path independently enforces the same invariant instead of relying implicitly on upstream execution having already enforced it.

### Proof of Concept
Not independently reproducible from the current codebase because the only exercised path (via blockifier's execution) already rejects undeclared class hashes before the OS ever sees them — see `deprecated_syscalls_test.rs::test_replace_class`, which shows the blockifier's Rust code already errors with `"is not declared"` for an undeclared class hash [6](#0-5) , and `syscall_tests/replace_class.rs::undeclared_class_hash`/`cairo0_class_hash` confirm the same guard on the current syscall path [7](#0-6) . The gap is confirmed purely by code inspection of the OS Cairo source, which lacks any equivalent assertion and explicitly flags this via a `TODO` [8](#0-7) . I was unable to fully verify whether any currently-active execution path (e.g., a native/Cairo-native syscall handler or an alternate SNOS-only entry point) invokes `execute_replace_class` without first passing through the blockifier's declared-class check — a Devin session with full repository access and the ability to trace all callers of this OS function would be needed to confirm whether this is purely latent/defense-in-depth or is exploitable today.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-404)
```rust
#[test]
fn test_replace_class() {
    // Negative flow.
    let chain_info = &ChainInfo::create_for_testing();
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo0);
    let empty_contract = FeatureContract::Empty(CairoVersion::Cairo0);
    let mut state = test_state(chain_info, Fee(0), &[(test_contract, 1), (empty_contract, 1)]);
    let test_address = test_contract.get_instance_address(0);
    // Replace with undeclared class hash.
    let calldata = calldata![felt!(1234_u16)];
    let entry_point_call = CallEntryPoint {
        calldata,
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err().to_string();
    assert!(error.contains("is not declared"));

    // Positive flow.
    let old_class_hash = test_contract.get_class_hash();
    let new_class_hash = empty_contract.get_class_hash();
    assert_eq!(state.get_class_hash_at(test_address).unwrap(), old_class_hash);
    let entry_point_call = CallEntryPoint {
        calldata: calldata![new_class_hash.0],
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    entry_point_call.execute_directly(&mut state).unwrap();
    assert_eq!(state.get_class_hash_at(test_address).unwrap(), new_class_hash);
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
