Confirmed: this is a genuine, currently-open gap in the Starknet OS Cairo code, explicitly flagged by its own `TODO(Yoni, 1/1/2026)` comment, and it diverges from the Rust `blockifier` crate's equivalent logic which does perform the check.

### Title
Starknet OS `execute_replace_class` skips declared-class and Cairo version validation, diverging from Blockifier's enforcement - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Starknet OS Cairo implementation of the `replace_class` syscall unconditionally overwrites a contract's `class_hash` in `contract_state_changes` without validating that the target `class_hash` corresponds to a declared class, and without validating that the class is Cairo1 (V1). This mirrors the audited bug class "no validation of contract types": a critical field (`class_hash`) is consumed and wired into state without checking it matches the expected type/invariant, whereas the parallel Rust `blockifier` execution engine used for actual block building does perform this validation.

### Finding Description
The OS syscall handler for `REPLACE_CLASS_SELECTOR` calls `execute_replace_class`, which reads `request.class_hash` and directly builds a new `StateEntry` with that class hash, with only a `TODO` comment acknowledging the missing check: [1](#0-0) 

By contrast, the Rust `blockifier` crate's equivalent syscall handler explicitly requires the class to be declared (`get_compiled_class` fails with `UndeclaredClassHash` otherwise) and explicitly forbids replacing with a Cairo0 (V0) class: [2](#0-1) 

This is confirmed by the corresponding negative-flow unit tests, `undeclared_class_hash` and `cairo0_class_hash`, which assert the Blockifier rejects such calls with `"is not declared"` and `"Cannot replace V1 class hash with V0 class hash"` respectively: [3](#0-2) 

The deprecated (Cairo0) syscall path in the OS has the identical gap: [4](#0-3) 

The Starknet OS is re-executed independently of the Blockifier (e.g., during proof generation / re-execution verification, see `crates/blockifier_reexecution`). Since it is a separate re-implementation of the same state-transition function, both must reject the exact same set of syscalls to guarantee the same resulting state diff. Because the OS lacks the declared-class and Cairo-version checks that the Blockifier enforces, a `replace_class` syscall targeting an undeclared class hash, or targeting a Cairo0 class hash, would be **rejected/reverted by the Blockifier** during actual execution but **silently accepted by the OS** during re-execution, producing two different state diffs (and therefore two different committed roots) for the same transaction.

### Impact Explanation
This causes a concrete state/root divergence between the Blockifier (source of truth for on-chain state commitment) and the Starknet OS (source of truth for proof generation and re-execution verification). Any transaction containing a `replace_class` syscall with an undeclared class hash or a V0 class hash will be reverted on-chain by the Blockifier, yet the OS re-execution would compute a class-hash overwrite for that contract that never actually happened, leading to a wrong committed state root / proof mismatch — a direct instance of "wrong committed root or block hash, honest-node divergence" as scoped.

### Likelihood Explanation
Trivially reachable: any account contract can invoke the `replace_class` syscall (Cairo1) or the deprecated `replace_class` syscall (Cairo0) with an attacker-chosen `class_hash`, e.g. via `test_replace_class` calling `replace_class_syscall(class_hash)`. No special privileges are required beyond being able to submit a normal transaction that calls a contract exposing this syscall.

### Recommendation
Add the same validation currently present in `blockifier::execution::syscalls::syscall_base::replace_class` to the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`): verify the class is declared (fail if not), and reject Cairo0 class hashes for the modern `replace_class` syscall, matching the Blockifier's `ForbiddenClassReplacement` behavior, before completing the `TODO(Yoni, 1/1/2026)` item.

### Proof of Concept
1. Deploy a Cairo1 account/contract exposing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)` (as in `blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo:201-203`).
2. Submit an invoke transaction calling `test_replace_class` with an undeclared `class_hash` (or a declared Cairo0 class hash).
3. In the Blockifier's actual execution path, this syscall fails with `"is not declared"` (or `"Cannot replace V1 class hash with V0 class hash"`), causing the transaction to revert — no class-hash change is committed.
4. When the Starknet OS re-executes the same block/transaction (`execute_replace_class` in `syscall_impls.cairo`), it has no equivalent check: it unconditionally writes the new `class_hash` into `contract_state_changes` for the contract, producing a state diff that the actual chain never committed.
5. The resulting OS-computed state commitment (and any proof built on it) diverges from the Blockifier-committed state root for the same block.

### Citations

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
