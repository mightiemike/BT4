### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by Blockifier, causing honest-node re-execution divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall lets any contract (reachable from an ordinary `Invoke` transaction) rewrite its own class hash in state. Blockifier's Rust syscall implementation enforces that the target `class_hash` is actually declared before performing the replacement, but the Starknet OS's Cairo re-implementation of the same syscall — used for proving/re-execution — has an explicit `TODO` marking that this check is missing, and performs the state update unconditionally.

### Finding Description
Blockifier's deprecated syscall handler explicitly guards the replacement: [1](#0-0) 
which calls `state.get_compiled_class(request.class_hash)?` — a call that returns `StateError::UndeclaredClassHash` if the class was never declared, as proven by the existing negative-flow test: [2](#0-1) 

The modern (Cairo1) syscall path in Blockifier delegates to the same `base.replace_class` check as well (via `syscall_base.rs`/native handler), so on the execution side the "declared" invariant is uniformly enforced.

In contrast, the Starknet OS's own Cairo implementation of the syscall (used when re-executing a block for proving) performs the class replacement in the state dictionary directly, with a comment acknowledging the missing check: [3](#0-2) 
Note the `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` at line 902 — no lookup or assertion against declared classes occurs before `dict_update` sets the new `class_hash` for the contract. The deprecated-syscall variant of the OS program has the identical gap: [4](#0-3) 

This is structurally analogous to the Visor Finance bug class described in the report: a permission/precondition check that exists in one code path but is missing in a parallel code path that handles the same privileged state mutation, letting an attacker-controlled input (here, an arbitrary, possibly-undeclared `class_hash`) reach a state-mutating operation without validation.

### Impact Explanation
Because Blockifier rejects `replace_class` calls that target an undeclared class hash (the call reverts with an error, and if this happens inside a transaction it may cause the call/transaction to fail or revert), while the OS accepts and commits the exact same state mutation unconditionally, the OS's re-executed state diff and root can diverge from the state Blockifier actually produced/committed by the sequencer during block building. This is a direct violation of the "Starknet OS re-execution must match sequencer execution" invariant: it can lead to wrong committed state (or an inability for the OS/prover to reproduce the sequencer's execution trace, since the OS treats a code path as succeeding that Blockifier would have rejected), i.e., honest-node/OS divergence and a wrong committed root for the affected contract's class hash.

### Likelihood Explanation
Any unprivileged transaction sender can trigger this by invoking a contract that calls `replace_class` (a routine, permissionless syscall used e.g. for proxy upgrade patterns) with a class hash that has not been declared on-chain. No special privileges, timing, or race condition are required — the divergence is purely a difference between two syscall implementations for the identical opcode/selector (`REPLACE_CLASS_SELECTOR`), and the omission is explicitly flagged as a known gap (`TODO`) in the source, confirming it is unaddressed at the time of this scan.

### Recommendation
Add the equivalent "class must be declared" check to both `execute_replace_class` implementations in the Starknet OS Cairo program (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) before performing the `dict_update`, mirroring the `get_compiled_class`/declared-class lookup that Blockifier performs, so that the OS rejects (or fails consistently) exactly the same set of `replace_class` calls that Blockifier rejects.

### Proof of Concept
1. Deploy a contract (Cairo0 or Cairo1) that exposes an external entrypoint calling the `ReplaceClass` syscall with an attacker-supplied `class_hash`, e.g. the pattern in `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo:513-516` (`test_replace_class`).
2. Submit an ordinary `Invoke` transaction from an unprivileged account calling this entrypoint with a `class_hash` that has never been declared on-chain.
3. In Blockifier's execution (as exercised by `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29` and `crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs:375-392`), the call fails with an "is not declared" error.
4. When the Starknet OS re-executes/proves the same block (via `execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo`), no declared-class check is performed, so the OS would compute a different resulting state (or fail to reconcile with Blockifier's rejection), demonstrating the code-path mismatch. Confirming the exact end-to-end consequence (transaction revert vs. hard OS abort vs. successful-but-divergent commit) would require running the OS test harness (`starknet_os_flow_tests`) against this scenario, which was not executed as part of this static review.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-392)
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
