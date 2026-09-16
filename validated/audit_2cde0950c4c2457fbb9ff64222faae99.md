### Title
Starknet OS `execute_replace_class` syscall implementation omits the "class must be declared" check enforced by the blockifier - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The blockifier's native implementation of the `replace_class` syscall validates that the target `class_hash` is both declared and a Cairo1 class before mutating contract state [1](#0-0) . The Starknet OS's Cairo re-implementation of the same syscall, `execute_replace_class`, performs no such check and instead carries an explicit unresolved `TODO` acknowledging the missing validation [2](#0-1) .

### Finding Description
Any contract can invoke the `replace_class` syscall from its own `__execute__` entry point, reachable by an unprivileged transaction sender via a normal `INVOKE` transaction.

In the blockifier (the execution engine used by the sequencer to build blocks), `replace_class` is guarded:
```
pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
    // Ensure the class is declared (by reading it), and of type V1.
    let compiled_class = self.state.get_compiled_class(class_hash)?;
    if !is_cairo1(&compiled_class) {
        return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
    }
    self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
    Ok(())
}
``` [1](#0-0) 
This is confirmed by dedicated blockifier tests that assert the transaction reverts with "is not declared" for an undeclared hash and "Cannot replace V1 class hash with V0 class hash" for a Cairo0 target [3](#0-2) .

The Starknet OS's Cairo implementation of the identical syscall, used during OS re-execution/proving of the block, contains no equivalent check:
```
func execute_replace_class{...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(...);
    ...
}
``` [2](#0-1) 
The Cairo0-syscall equivalent, `execute_replace_class` in `deprecated_execute_syscalls.cairo`, similarly performs no declared-class check [4](#0-3) .

This mirrors the reported bug class exactly: a state-mutating operation ("swap"/"transfer" analog = class replacement) omits a validation check present in the "reference" implementation (the blockifier), allowing an operation to succeed in one code path that should be rejected, producing an inconsistent/incorrect result relative to the trusted execution engine.

### Impact Explanation
The Starknet OS is executed to re-derive and commit the state diff/root that is ultimately posted and verified on L1; its output must match the blockifier's actual block execution for the chain to remain sound. Because `execute_replace_class` in the OS blindly writes any attacker-supplied `class_hash` (including undeclared hashes, or Cairo0 hashes for a Cairo1-only replacement path) into `contract_state_changes`, while the blockifier would revert the exact same transaction, the OS's computed state diff/root can diverge from the state actually produced (or rejected) by the sequencer's blockifier for the identical transaction. This is a wrong-committed-root / honest-node-divergence class issue: the OS-computed state commitment would not match the sequencer's real post-state, undermining the correctness guarantee that the OS output represents the verified execution of the block.

### Likelihood Explanation
Any account contract can trigger `replace_class` with an arbitrary felt as `class_hash` via a single ordinary transaction — no special privileges are required. The missing check is unconditional (not behind a feature flag) and explicitly flagged as an outstanding `TODO` in the OS source, meaning it reproduces on the current code path every time this syscall is exercised with an undeclared or wrong-version class hash.

### Recommendation
Add the same validation to the OS's `execute_replace_class` (in `syscall_impls.cairo`, and the deprecated equivalent) that the blockifier performs: verify the target `class_hash` corresponds to a declared compiled class (e.g., via the existing `compiled_class` guessing/validation machinery) and, for the new-syscalls path, that it is a Cairo1 class, failing the syscall the same way the blockifier does before applying the `contract_state_changes` dict update.

### Proof of Concept
1. Deploy a Cairo1 contract exposing a wrapper around the `replace_class` syscall (e.g., the existing `test_replace_class` test entry point pattern).
2. Submit an `INVOKE` transaction calling that entry point with an undeclared `class_hash` (or a Cairo0 class hash).
3. Observe blockifier execution reverts with "is not declared" / "Cannot replace V1 class hash with V0 class hash" per `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`.
4. Because the OS's `execute_replace_class` performs no such check, an OS re-execution/simulation of the equivalent syscall sequence would accept the write and update `contract_state_changes` for the contract to the invalid `class_hash`, diverging from the blockifier's rejection of the same operation.

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
