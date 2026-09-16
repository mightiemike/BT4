### Title
Missing "class is declared" validation in the Starknet OS `replace_class` syscall implementation causes divergence from Blockifier execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `replace_class` syscall lets any contract change its own `class_hash` in-place. In the Blockifier (the component that actually executes transactions when building a block), this syscall validates that the target `class_hash` was previously declared, and rejects the call otherwise. In the Starknet OS Cairo re-implementation of the same syscall (used for re-execution / proving), this validation is explicitly missing, marked only by a `TODO`.

### Finding Description
The Blockifier's VM syscall handler for `replace_class` reads the compiled class before writing the new class hash, so an attempt to switch a contract to an undeclared class fails: [1](#0-0) 

The corresponding tests confirm this enforced behavior — an undeclared class hash produces the error `"is not declared"`, and mixing Cairo0/Cairo1 class versions is also rejected: [2](#0-1) 

The Native syscall handler routes through the same base logic that performs this check: [3](#0-2) 

In contrast, the Starknet OS's Cairo implementation of the exact same syscall (which is executed during OS re-execution to build the proof and derive the committed state) performs `dict_update` directly on `contract_state_changes` with the requested `class_hash`, with the validation explicitly deferred via a TODO comment: [4](#0-3) 

The same pattern (missing declared-class check) also exists in the deprecated syscall path of the OS: [5](#0-4) 

This is a direct analog of the report's core bug class — a state-mutating operation ("swap the reference to a new object", collateral address vs. class hash) that omits a validation check present in another privileged/parallel code path, letting an unprivileged transaction sender race ahead of, or diverge from, the system's expected invariant enforcement.

### Impact Explanation
The Starknet OS's execution trace is what gets proven and is the ultimate source of truth for the committed state root and block hash re-derived during Starknet OS re-execution. If the Blockifier (sequencer execution) and the OS (re-execution/proving) disagree on whether a `replace_class` call to an undeclared class hash should succeed, one of two things happens:
- The block built by the sequencer (where the transaction reverted due to Blockifier's check) is re-executed by the OS, which would not revert the same call, producing a different state entry (`class_hash`) for that contract than what the sequencer actually committed — an honest-node divergence, and potentially a wrong computed state commitment / block hash.
- Alternatively, since undeclared classes have no compiled CASM, any subsequent call into the contract under its new (undeclared) class in the OS's simulated view could not actually be executed consistently, risking an inconsistent or unprovable state.

Either outcome falls under "wrong committed root or block hash" / "honest-node divergence" categories.

### Likelihood Explanation
The syscall is trivially reachable: any Cairo1 contract that exposes a wrapper around `replace_class_syscall` (a completely ordinary, unprivileged operation) can be invoked by any transaction sender with an undeclared `class_hash` as the argument, as demonstrated by the test contract used in Blockifier's own tests: [6](#0-5) 
No privileged role, staking, or special network condition is required — a single submitted transaction suffices.

### Recommendation
Implement the missing "class is declared" check (and matching Cairo-version compatibility check) in the Starknet OS's Cairo `execute_replace_class` implementations (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) to mirror the Blockifier's `syscall_base`/`hint_processor` logic exactly, ensuring the OS's re-execution enforces identical preconditions as the Blockifier before committing a `class_hash` change to `contract_state_changes`.

### Proof of Concept
1. Deploy a contract exposing `test_replace_class(class_hash)` which calls `replace_class_syscall(class_hash)`.
2. Submit an invoke transaction calling `test_replace_class` with an arbitrary, undeclared `class_hash`.
3. In the Blockifier (sequencer execution), this call reverts with `"is not declared"` per `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`; fee is charged but no class-hash write is committed.
4. When the same transaction is re-executed by the Starknet OS (`execute_replace_class` in `syscall_impls.cairo:881-920`), no declared-class check exists — the OS unconditionally overwrites `contract_state_changes` for the contract's `class_hash`, diverging from what the sequencer actually committed to the chain.

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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L395-406)
```rust
    fn replace_class(&mut self, class_hash: Felt, remaining_gas: &mut u64) -> SyscallResult<()> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.replace_class.base_syscall_cost(),
            SyscallSelector::ReplaceClass,
        )?;

        self.base
            .replace_class(ClassHash(class_hash))
            .map_err(|err| self.handle_error(remaining_gas, err))?;
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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
