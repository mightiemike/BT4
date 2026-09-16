# MintBatch-Style "Missing Implementation" Analog: `execute_replace_class` in the Starknet OS Skips the Declared-Class Validation Enforced by the Blockifier

### Title
Starknet OS `execute_replace_class` omits the "class is declared" check that the blockifier enforces, causing state-root divergence on `replace_class` syscall failures - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The external report describes a Solidity function (`mintBatch()`) that one code path calls but that is never actually implemented/validated elsewhere, creating a silent behavioral gap. The direct analog in this sequencer repo is the `replace_class` syscall: the blockifier's Rust implementation validates that the target class hash is declared (and Cairo-version-compatible) before mutating state, but the Cairo implementation used by the Starknet OS for transaction re-execution/proving contains an explicit `TODO` marking this validation as *not implemented*, and unconditionally performs the state mutation.

### Finding Description
In the blockifier (the component that actually builds blocks and is the source of truth for a sequencer node), `replace_class` is validated before it is allowed to succeed:
- The deprecated (Cairo0) syscall handler explicitly reads/validates the class before mutating state: [1](#0-0) 
- The VM/native syscall path dispatches to `base.replace_class`, and the accompanying tests confirm this path rejects undeclared class hashes and Cairo0/Cairo1 mismatches with explicit errors ("is not declared", "Cannot replace V1 class hash with V0 class hash"): [2](#0-1) [3](#0-2) 

However, the Starknet OS's own Cairo re-implementation of this syscall - used to independently re-execute transactions and compute/prove the committed state - has no such check. It contains an explicit acknowledgment that the check is missing:

```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
...
``` [4](#0-3) 

The deprecated Cairo0 OS syscall path has the identical gap, with no validation at all before mutating `contract_state_changes`: [5](#0-4) 

### Impact Explanation
Because Cairo1 contracts can invoke `replace_class_syscall` and pattern-match on its `SyscallResult` (i.e., catch a failure without reverting the whole transaction), the following divergence is directly reachable by an unprivileged transaction sender:
1. A contract calls `replace_class` with an undeclared class hash (or a Cairo0 hash on a Cairo1 contract), wrapping the call so a syscall failure is caught rather than propagated as a full-transaction revert.
2. The blockifier (native sequencer execution) rejects this specific syscall internally (per the validation shown above) — the contract's class hash is *not* changed in the state diff the sequencer commits to its own state and gas accounting.
3. The Starknet OS, when re-executing the very same transaction to compute the state commitment/Merkle root for the STARK proof, has no equivalent check and unconditionally applies the class-hash replacement to `contract_state_changes`.
4. The state root computed/committed via the OS therefore differs from the actual state maintained by the sequencer's own blockifier for the same block — a wrong committed root / honest-node divergence caused purely by a missing validation in one of two supposedly-equivalent execution engines.

This maps to "wrong committed root ... honest-node divergence" in the validation criteria, and is reachable from a single submitted transaction with no privileged actor required.

### Likelihood Explanation
Likelihood is moderate: it requires a Cairo1 contract that calls the low-level `replace_class_syscall` and handles the `Result` without immediately unwrapping/reverting (rather than the common `unwrap_syscall()` pattern used in most feature-contract examples), but this is a legitimate and permitted Cairo1 usage pattern, not a privileged or malicious-operator scenario — any account/contract deployer can construct such a contract and any transaction sender can invoke it.

### Recommendation
Implement the missing declared-class (and Cairo-version-compatibility) check in `execute_replace_class` in `syscall_impls.cairo` (and the Cairo0 equivalent in `deprecated_execute_syscalls.cairo`) so that the Starknet OS's behavior on `replace_class` failure exactly mirrors the blockifier's validation in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs` and `crates/blockifier/src/execution/syscalls/hint_processor.rs`/`syscall_base.rs`, before removing the `TODO`.

### Proof of Concept
Conceptual PoC (cannot be executed here, but the code paths above demonstrate the gap directly):
1. Deploy a Cairo1 contract with an external function that calls `replace_class_syscall(undeclared_class_hash)` and matches on the `Result`, e.g. using `match replace_class_syscall(hash) { Result::Ok(_) => ..., Result::Err(_) => { /* swallow error, continue execution */ } }`, so the transaction as a whole succeeds.
2. Submit an invoke transaction calling this function with an undeclared `class_hash`.
3. Observe (per [6](#0-5) ) that the blockifier's execution produces a syscall failure that is caught inside the contract - the sequencer's own state has the class hash unchanged.
4. Feed the same transaction into the Starknet OS execution path exercising `execute_replace_class` in `syscall_impls.cairo` — because of the missing check flagged by the `TODO`, the OS applies the class-hash change to `contract_state_changes` unconditionally, producing a different state diff/commitment than the blockifier's own state for the same transaction.

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
