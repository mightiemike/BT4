### Title
Missing "class must be declared" check in Starknet OS `execute_replace_class` allows honest-node/OS divergence from Blockifier execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall (`execute_replace_class`) commits a contract's new `class_hash` to `contract_state_changes` without first verifying that the class hash was actually declared. This validation exists in the Rust Blockifier's syscall handler but is missing — with an explicit TODO acknowledging the gap — in the Starknet OS's own execution of the same syscall, creating a state-transition divergence between block production (Blockifier) and block re-execution/proving (Starknet OS).

### Finding Description
When a contract invokes the `replace_class` syscall, the Blockifier (used by the sequencer to build and validate blocks) enforces that the target class hash is declared before mutating state: [1](#0-0) 

```rust
fn replace_class(...) -> DeprecatedSyscallResult<ReplaceClassResponse> {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler.state.set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;
    Ok(ReplaceClassResponse {})
}
```
This is confirmed by tests that explicitly check an undeclared class hash is rejected: [2](#0-1) .

However, the Starknet OS's Cairo re-implementation of the identical syscall for Cairo1 (`execute_replace_class` in `syscall_impls.cairo`) skips this check entirely, and even leaves an explicit TODO acknowledging it: [3](#0-2) 

```
// Replaces the class.
func execute_replace_class{...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;
    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address, prev_value=cast(state_entry, felt), new_value=cast(new_state_entry, felt),
    );
    ...
}
```
The same missing-check pattern also exists in the deprecated Cairo0 syscall path: [4](#0-3) 

The mutated `contract_state_changes` dict feeds directly into the contract-state Merkle-tree commitment computation (`compute_class_commitment`/state commitment logic reads `contract_state_changes`), and there is no separate cross-check elsewhere in the OS pipeline (e.g., `validate_compiled_class_facts`, which only validates classes explicitly declared via a Declare transaction, not classes referenced via `replace_class`) that would catch an undeclared or unvalidated class hash being written via this path: [5](#0-4) .

### Impact Explanation
Since this syscall is directly reachable by any unprivileged contract via a single Invoke transaction calling `replace_class_syscall`, an attacker can cause the OS's state transition for a contract to accept a `class_hash` value that the Blockifier would have rejected (undeclared, or in the Cairo0/Cairo1 mismatch case that the Blockifier explicitly forbids — see `cairo0_class_hash` test at replace_class.rs:31-53). This is a concrete state-transition-function divergence between the two independent implementations of the same syscall: Blockifier (execution/validation) vs Starknet OS (re-execution/proof generation). Such a divergence can lead to a wrong committed state root/class-hash-at-address being accepted by the OS/proving pipeline that differs from what the sequencer's execution layer computed, i.e., an honest-node divergence and potentially an incorrectly proven state transition.

### Likelihood Explanation
The syscall is callable by any contract from an ordinary transaction with no special privileges, making the trigger trivially reachable. The only barrier is that the divergence must actually occur during OS re-execution/proving, which happens for every block, not just adversarial ones.

### Recommendation
Add the equivalent declared-class check in `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`, mirroring the Blockifier's check (verify the class hash exists among the declared/compiled class facts guessed for the block, e.g., via `compiled_class_facts`/`contract_class_changes`) before writing it to `contract_state_changes`, removing the outstanding TODO and closing the gap between the two execution engines.

### Proof of Concept
1. Deploy a Cairo1 contract exposing `replace_class_syscall`.
2. Submit an Invoke transaction calling this entry point with an arbitrary, never-declared `class_hash` felt (e.g., `1234`).
3. Under Blockifier execution, this reverts with "is not declared" (as demonstrated by `undeclared_class_hash` test at `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`).
4. Under the Starknet OS's `execute_replace_class` (`syscall_impls.cairo:881-920`), the same call would succeed and write the arbitrary class hash into `contract_state_changes` without any validation, since the check is a documented TODO — demonstrating a re-execution result inconsistent with the Blockifier's rejection.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L83-95)
```text
// Validates the compiled class facts structure and hash after the execution.
// Uses the execution info to optimize hash computation.
func validate_compiled_class_facts_post_execution{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts,
        builtin_costs=builtin_costs,
    );

    return ();
}
```
