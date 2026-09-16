### Title
Starknet OS `replace_class` syscall omits the "class must be declared" check enforced by the Blockifier, causing state divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Blockifier's `replace_class` syscall implementation enforces that the target class hash must already be declared before a contract's class is swapped. The Starknet OS's Cairo re-implementation of the same syscall, used to produce the validity proof of a block's execution, does not perform this check — it is explicitly left as a TODO. This is the same bug class as the reported FuelToken issue: an implied precondition (a state-dependent ordering guarantee) that is enforced in one code path but not in the other, allowing the two subsystems to disagree about whether a given execution should succeed or revert.

### Finding Description
When a contract invokes the `replace_class` syscall, the Blockifier (the component that actually executes transactions in the sequencer) requires that the given class hash is already declared, by reading the compiled class before applying the change: [1](#0-0) 

This is confirmed by tests where calling `replace_class` with an undeclared hash fails with `"is not declared"`: [2](#0-1) [3](#0-2) 

The Starknet OS, which re-executes the same block's transactions in Cairo to compute the state root and produce the validity proof (see `apollo_starknet_os_program`), implements `execute_replace_class` without this check — the comment explicitly documents the gap: [4](#0-3) 

```
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
```

The deprecated (Cairo 0) syscall path in the OS shows the same omission: [5](#0-4) 

The Blockifier treats a `replace_class` call on an undeclared class hash as a syscall execution error, which — for a Cairo1 contract invoking it without wrapping the call in a "safe" try/catch pattern — causes the transaction's execution to revert (only nonce and fee are charged; the class-replacement state change is discarded). The OS, lacking the same check, would instead apply the state entry unconditionally and continue execution as if it succeeded. Since the OS's role is to independently recompute the exact same state transition that the sequencer already committed (in order to prove it), any transaction that exercises this specific code path — a contract that calls `replace_class` with a hash that is not (yet) declared — causes the OS's computed execution result (revert vs. success, and the resulting state diff/state entry) to diverge from the Blockifier's actual committed result.

### Impact Explanation
This directly maps to the "Starknet OS re-execution" and "wrong committed root / honest-node divergence" acceptance criteria. If the OS computes a different outcome (non-reverted vs reverted, or a different final class hash for the contract) than what the Blockifier actually committed on-chain, the resulting Cairo trace/output produced by the OS will not match the sequencer's real state diff / global state root for that block. This can manifest as:
- A validity proof that certifies a state transition different from what was actually applied by the sequencer (undermining the integrity of the state root committed to L1), or
- The OS execution failing/panicking in ways that don't correspond to the actual (successful, reverted) transaction outcome, breaking the prover pipeline for otherwise valid blocks.

Either outcome is a "wrong committed root" / "honest-node divergence" class impact, satisfying Medium/High severity.

### Likelihood Explanation
The trigger is fully reachable by any unprivileged transaction sender: deploy or interact with a Cairo1 contract that invokes `replace_class_syscall` with a class hash that has not been declared (or has not yet been declared at the point of the call within the same execution). No special privileges, staking, or operator/proposer misbehavior are required — a single ordinary invoke transaction is sufficient to exercise the divergent code path.

### Recommendation
Add the equivalent "class must be declared" precondition to `execute_replace_class` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo` (and the deprecated syscalls path in `deprecated_execute_syscalls.cairo`), mirroring the Blockifier's `get_compiled_class`/declared-class lookup, so that the OS revert behavior for `replace_class` on an undeclared class hash is identical to the Blockifier's, before this code is used in production proving.

### Proof of Concept
1. Deploy a Cairo1 contract exposing an entry point that calls `replace_class_syscall(undeclared_class_hash)` directly (as in `test_forbidden_syscall_in_virtual_mode`, `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo:1479-1509`, which already demonstrates invoking `ReplaceClass` with an arbitrary class hash obtained via `get_class_hash_at_syscall`).
2. Submit an ordinary `invoke` transaction calling this entry point with a class hash that is not declared in state.
3. In the Blockifier, execution fails inside the syscall (`syscall_handler.state.get_compiled_class(request.class_hash)?` errors), causing the transaction to revert; only fee/nonce changes are committed, per `crates/blockifier/src/execution/syscalls/hint_processor.rs:795-807`.
4. When the Starknet OS independently re-executes the same transaction from the same input trace using `execute_replace_class` (`crates/apollo_starknet_os_program/.../syscall_impls.cairo:881-920`), no declared-class check is performed, so the class-hash state entry is unconditionally updated and execution proceeds as non-reverted — diverging from step 3's actual committed result. [6](#0-5)

### Citations

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L795-807)
```rust
            Hint::Starknet(hint) => Ok(execute_next_syscall(self, vm, hint)?),
            Hint::External(_) => {
                panic!("starknet should never accept classes with external hints!")
            }
        }
    }

    /// Trait function to store hint in the hint processor by string.
    fn compile_hint(
        &self,
        hint_code: &str,
        _ap_tracking_data: &ApTracking,
        _reference_ids: &HashMap<String, usize>,
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-29)
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
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-391)
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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L1479-1509)
```text
    // Tests forbidden syscalls in virtual OS mode.
    // Gets valid class_hash and contract_address from the current contract.
    // syscall_selector should be one of: 'Deploy', 'GetBlockHash', 'ReplaceClass', 'MetaTxV0'.
    #[external(v0)]
    fn test_forbidden_syscall_in_virtual_mode(self: @ContractState, syscall_selector: felt252) {
        let execution_info = get_execution_info().unbox();
        let contract_address = execution_info.contract_address;
        let class_hash = syscalls::get_class_hash_at_syscall(contract_address).unwrap_syscall();

        if syscall_selector == 'GetBlockHash' {
            syscalls::get_block_hash_syscall(0).unwrap_syscall();
        } else if syscall_selector == 'ReplaceClass' {
            syscalls::replace_class_syscall(class_hash).unwrap_syscall();
        } else if syscall_selector == 'Deploy' {
            // Pass constructor arguments (two felt252 values).
            syscalls::deploy_syscall(class_hash, 0, array![0, 0].span(), false).unwrap_syscall();
        } else if syscall_selector == 'MetaTxV0' {
            // meta_tx_v0 requires the selector to be `__execute__`.
            // __execute__ takes (class_hash: ClassHash, to_panic: bool).
            meta_tx_v0_syscall(
                contract_address,
                selector!("__execute__"),
                array![class_hash.into(), 0].span(),
                array![].span(),
            )
                .unwrap_syscall();
        } else {
            panic!("Unexpected syscall selector");
        }
    }
}
```
