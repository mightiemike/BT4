## Analysis

The external report's bug class is "a state-mutating action accepts external input without validating a required relationship against existing/expected data" (Strategy's `want` vs. Lender's `want`). The strongest analog in this Starknet sequencer codebase is in the **Starknet OS (`starknet_os_program`)** implementation of the `replace_class` syscall, which mutates a contract's class hash in state without validating that the new class hash corresponds to a declared class (or even a Cairo1 class), unlike the "real" execution engine (`blockifier`) which does perform this validation. This creates a state-transition divergence between block building (Blockifier) and OS re-execution/proving.

### Title
Starknet OS `execute_replace_class` omits declared-class and Cairo-version validation performed by Blockifier, causing sequencer/OS state divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Cairo Starknet OS's implementation of the `replace_class` syscall (`execute_replace_class`) unconditionally overwrites a contract's class hash in `contract_state_changes` with the caller-supplied `class_hash`, with an explicit `TODO` acknowledging the missing check. In contrast, the Rust Blockifier implementation of the same syscall validates that the target class is declared and is a Cairo1 (V1) class before allowing the replacement, rejecting the call otherwise.

### Finding Description
In `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`, `execute_replace_class` reads the requested `class_hash` and directly writes a new `StateEntry` with that class hash into `contract_state_changes`, with no verification step: [1](#0-0) 
The code contains an explicit acknowledgment of the gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The equivalent legacy path in `deprecated_execute_syscalls.cairo` has the same unchecked behavior and doesn't even have visibility into `contract_class_changes` to perform such a check: [2](#0-1) 

By contrast, the Rust Blockifier — which is the component that actually executes transactions when the sequencer builds a block — enforces two checks in `syscall_base.rs::replace_class`: (1) the class must be declared (`self.state.get_compiled_class(class_hash)?` returns an error like `"is not declared"` otherwise), and (2) the class must be Cairo1/V1 (`is_cairo1(&compiled_class)`, otherwise `ForbiddenClassReplacement`): [3](#0-2) 
These checks are exercised and confirmed by tests such as `undeclared_class_hash` and `cairo0_class_hash`, which assert the call fails with "is not declared" / "Cannot replace V1 class hash with V0 class hash": [4](#0-3) 

Since the Starknet OS is meant to faithfully re-execute (and prove) exactly what Blockifier computed when building the block, this asymmetry means: for any contract that calls `replace_class_syscall` with an undeclared class hash or a Cairo0 (deprecated) class hash, Blockifier will revert the call (leaving the contract's class hash unchanged, only charging fees for the reverted call), while the unprivileged Cairo OS program will unconditionally accept the replacement and mutate `contract_state_changes` accordingly.

### Impact Explanation
This is directly reachable by any unprivileged account executing a single transaction that invokes `replace_class_syscall(class_hash)` with a class hash that is undeclared or belongs to a Cairo0 class. It produces a state root/committed state divergence: the state actually committed on L2 (per Blockifier's execution during block building) differs from the state the Starknet OS computes and proves during re-execution. This falls squarely under "wrong committed root or block hash" / "honest-node divergence" since two supposedly-equivalent execution engines (Blockifier at block-building time and the Cairo OS at proving/re-execution time) produce different final contract class hashes for the same transaction, undermining the soundness of state commitments and potentially STARK proofs generated for the block.

### Likelihood Explanation
Trivial to trigger: any account contract with logic that calls the `replace_class` syscall with attacker-influenced or arbitrary calldata (a common pattern in proxy/upgradeable contracts) can pass an undeclared or Cairo0 class hash. No special privileges, timing, or coordination are required — a single transaction from any unprivileged sender suffices.

### Recommendation
Add the same validation in the Cairo OS's `execute_replace_class` (both `syscall_impls.cairo` and the deprecated path) that Blockifier performs: verify the supplied `class_hash` is present/declared (e.g., via `contract_class_changes`/declared-classes tracking passed into the function) and, for the new-syscalls path, that it is not a Cairo0 (deprecated) class, mirroring the `is_cairo1` check and error behavior in `syscall_base.rs::replace_class`. Resolve the outstanding `TODO(Yoni, 1/1/2026)` comment accordingly before this divergence can be exploited.

### Proof of Concept
1. Deploy any Cairo1 account/contract instance whose code calls `replace_class_syscall(class_hash)` with a caller-supplied `class_hash` (e.g., the test helper `test_replace_class` pattern used in `blockifier_test_utils` feature contracts).
2. Submit an invoke transaction that calls this entry point with `class_hash` set to a value that has never been declared (or that is declared but is a Cairo0/V0 class).
3. Observe: Blockifier (actual sequencer execution) reverts the inner call (see `undeclared_class_hash`/`cairo0_class_hash` tests at [5](#0-4) ), leaving the contract's on-chain class hash unchanged.
4. When the same transaction is re-executed/proven by the Starknet OS program (`execute_replace_class` in `syscall_impls.cairo`), no equivalent check exists, so the class hash of the contract is unconditionally overwritten in `contract_state_changes`, producing a different final state/state-diff than what Blockifier committed — a concrete state/root divergence between the two engines for identical input.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L887-920)
```text
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
