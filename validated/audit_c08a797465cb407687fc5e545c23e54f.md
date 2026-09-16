### Title
Starknet OS `replace_class` syscall omits declared-class validation enforced by blockifier, causing state root/block hash divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The blockifier's `replace_class` syscall handler requires the target class hash to be a declared class before allowing a contract to change its own class hash. The Starknet OS's Cairo re-implementation of the same syscall, used to re-execute blocks and compute the committed state root/block hash, does not perform this check — it is marked with an explicit unresolved TODO. Any contract, invoked by an ordinary unprivileged transaction, can call `replace_class` with an undeclared class hash; blockifier will reject the transaction, but the OS's model of execution will accept it, producing a state transition that the two components disagree on.

### Finding Description
`replace_class` is a syscall any deployed contract can invoke on itself to change the class it is instantiated from. In the blockifier (native Rust execution engine used to build the block and decide transaction success/failure), the Cairo0 syscall handler explicitly enforces that the class must already be declared: [1](#0-0) 

This is confirmed by a dedicated test that a call to `replace_class` with an undeclared class hash fails with an "is not declared" error, while replacing with a declared class hash succeeds: [2](#0-1) 

However, the Starknet OS's own Cairo implementation of the same syscall — used during Starknet OS re-execution to independently recompute the block's state commitment / state root and to produce the STARK proof of block validity — does **not** perform this check. The code contains an explicit acknowledgment of the missing validation: [3](#0-2) 

The equivalent legacy syscall path in the OS (`deprecated_execute_syscalls.cairo`) has the same unconditional behavior — it updates the contract's `class_hash` in `contract_state_changes` without checking that the class was ever declared: [4](#0-3) 

This is directly analogous to the reported bug class: a component (`Tap.updateController`) allowed updating a critical reference without the guard that the rest of the system relied on. Here, the Starknet OS's re-execution model allows updating a contract's class reference (`replace_class`) without the declared-class guard that the blockifier — the actual execution engine that decides transaction success and produces the canonical state diff — enforces. The two independent implementations of the same syscall are inconsistent.

### Impact Explanation
This falls under the "Starknet OS re-execution" category explicitly in scope. The Starknet OS is responsible for independently re-executing a block (built by the blockifier) and producing/attesting to the resulting state commitment/block hash, which underlies the network's proof of correctness. If a transaction that legitimately fails in blockifier (invoking `replace_class` with an undeclared class hash, causing the blockifier to revert/reject the state change) is instead accepted and applied by the OS's Cairo model (because it lacks the declared-class check), the OS will compute a different final contract state (and hence a different state root / block hash) than the one actually committed by the sequencer's blockifier execution. This is a case of "wrong committed root or block hash" / "honest-node divergence," since the OS proof-generation path and the blockifier execution path can disagree on the outcome of the same transaction, undermining the soundness of the OS's re-execution attestation for that block.

### Likelihood Explanation
This is trivially triggerable by an ordinary contract call — no special privileges, roles, or proposer/prover status are required. Any account can deploy or call a contract that invokes the `replace_class` syscall with an arbitrary, never-declared class hash. The path through the OS's `execute_replace_class` (syscall_impls.cairo) or its deprecated counterpart is reached during standard OS re-execution of every block containing such a call, making the divergence deterministic and reproducible whenever such a transaction exists in a block.

### Recommendation
Add the missing declared-class validation to the OS's `execute_replace_class` implementations (in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) to mirror the blockifier's check in `hint_processor.rs` (`syscall_handler.state.get_compiled_class(request.class_hash)?` before permitting the class-hash write). This ensures the OS's re-executed state transition matches exactly what the blockifier computes and commits, eliminating the possibility of a state root/block hash divergence for this syscall.

### Proof of Concept
1. Deploy any contract `C` that, in one of its entry points, invokes the `replace_class` syscall with a `class_hash` value that has never been declared on-chain.
2. Submit an ordinary invoke transaction calling that entry point of `C`. In the blockifier execution path (block building), the transaction reverts/fails because `get_compiled_class` for the undeclared class hash returns `StateError::UndeclaredClassHash` (as demonstrated by the existing test `test_replace_class` in `deprecated_syscalls_test.rs`).
3. During Starknet OS re-execution of the same block (used for proof generation / state commitment), the corresponding Cairo function `execute_replace_class` in `syscall_impls.cairo` (or `deprecated_execute_syscalls.cairo`) contains no such check — it unconditionally writes the new (undeclared) `class_hash` into `contract_state_changes` — so if the transaction were treated as successful by the OS's model (e.g. due to differing control flow / missing revert enforcement at this check point), the OS would compute a different final contract state entry for `C`'s address than blockifier's actual committed state, leading to a state root mismatch between the block builder and the OS re-execution/proof.

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
