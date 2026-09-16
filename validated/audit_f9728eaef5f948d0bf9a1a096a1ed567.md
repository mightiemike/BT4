### Title
OS `execute_replace_class` Skips the Declared-Class Check Applied by Blockifier, Causing State Root Divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall unconditionally rewrites a contract's class hash in the state-change dictionary without verifying that the target class hash is actually declared (or that it is a Cairo1 class), while the blockifier's Rust implementation of the same syscall enforces both checks before mutating state. A transaction that blockifier would reject/revert (leaving state unchanged) is instead accepted and committed to the state trace by the OS during re-execution, producing a different post-state (and therefore a different computed state root) than what the sequencer actually committed.

### Finding Description
Any unprivileged account can invoke the `replace_class_syscall` from `__execute__` (as exercised by `test_replace_class` in the feature contracts). In the blockifier (the actual execution engine used to build and commit blocks), `SyscallHandlerBase::replace_class` requires the class to be declared and to be a Cairo1 class before mutating state: [1](#0-0) 

If the class hash is undeclared, or is a Cairo0 class hash, the call returns an error (`UndeclaredClassHash` / `ForbiddenClassReplacement`) and the entry point (and its state changes) are reverted, as validated by the test suite: [2](#0-1) 

The Starknet OS's Cairo re-implementation of this same syscall, used for proving/re-executing the block (`starknet_os`, `blockifier_reexecution`, OS flow tests), performs no such validation. It reads the current state entry and unconditionally writes a new one with the caller-supplied `class_hash`, with an explicit TODO acknowledging the missing check: [3](#0-2) 

The same gap exists in the deprecated Cairo0 syscall path: [4](#0-3) 

This is directly analogous to the Juicebox `[H-02]` bug class: a state-mutating operation (there, associating/transferring token ownership; here, replacing a contract's class hash) is performed by one code path without validating a precondition (there, actual ownership control; here, that the class is declared and is a valid Cairo1 class) that a parallel/trusted code path (the newer `JBController`/here, the blockifier) does enforce. The result is that the "authoritative" component's guarantee is silently bypassed by the second component that consumes the same input.

### Impact Explanation
Because blockifier is what actually executes transactions and computes the state diff that is committed on-chain, a `replace_class` call with an undeclared or Cairo0 class hash will fail/revert during real block building — the class hash is not changed and no such entry appears in the committed state diff. However, when the Starknet OS re-executes the identical block/transaction (for proof generation or in `blockifier_reexecution`/OS flow test tooling that is meant to reproduce the sequencer's exact state transition), it will apply the class hash change unconditionally. This yields an OS-computed final state (and Patricia root) that differs from the state actually committed by the sequencer for the same block — a wrong/divergent committed root, one of the explicitly accepted impact categories (wrong committed root / honest-node divergence). Depending on how this manifests in the proving pipeline, it can either produce an invalid proof for an honestly-executed block or mask an execution outcome discrepancy between the two engines that are supposed to agree bit-for-bit.

### Likelihood Explanation
The path is trivially reachable by any unprivileged account: simply invoke `replace_class_syscall` with a class hash that is either undeclared or a Cairo0 class hash (both trivial to construct, e.g. a random felt or any previously-declared Cairo0 class). No special privileges, timing, or off-chain assumptions are required — this is a single-transaction, single-syscall difference in behavior between the two components that must otherwise stay consistent.

### Recommendation
Add the same validation to the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that blockifier performs: verify the target `class_hash` corresponds to a declared class (and, for the new syscall, that it is specifically a Cairo1/Sierra class) before writing the new state entry, mirroring `SyscallHandlerBase::replace_class`'s `get_compiled_class` + `is_cairo1` checks. This closes the TODO already present in the code and restores state-transition equivalence between blockifier and the Starknet OS.

### Proof of Concept
1. Deploy any account contract.
2. From `__execute__`, call `replace_class_syscall(class_hash)` where `class_hash` is either (a) a felt that has never been declared, or (b) the class hash of a previously declared Cairo0 contract.
3. Submit as a normal invoke transaction to the sequencer. Blockifier's `replace_class` (`syscall_base.rs:369-378`) returns `UndeclaredClassHash`/`ForbiddenClassReplacement`, the call reverts, and the account's class hash in the committed state diff is unchanged (as demonstrated by `undeclared_class_hash`/`cairo0_class_hash` tests in `syscall_tests/replace_class.rs`).
4. Re-run the same block through the Starknet OS (`execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo`): since no declared-class check exists, the OS applies the class-hash change to its state dictionary, producing a state entry/state root that differs from the one blockifier committed in step 3.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L17-53)
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
