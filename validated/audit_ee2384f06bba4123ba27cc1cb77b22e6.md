## Confirmed Finding

Both the deprecated syscall path (`crates/apollo_starknet_os_program/.../deprecated_execute_syscalls.cairo:307-329`) and the current syscall path (`crates/apollo_starknet_os_program/.../execution/syscall_impls.cairo:881-920`) in the Starknet OS Cairo program implement `execute_replace_class` **without verifying that `class_hash` is a declared class** before writing it into `contract_state_changes` — the code even carries an explicit acknowledgment of this gap: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` [1](#0-0) 

By contrast, the Rust blockifier — the component that actually builds/validates blocks and is reachable by any unprivileged contract calling `replace_class` — enforces this check for both the deprecated and native/VM syscall executors: it explicitly reads the class (`state.get_compiled_class(request.class_hash)?`) before allowing the class-hash swap, erroring with "is not declared" otherwise. [2](#0-1) [3](#0-2) 

### Title
Missing declared-class check in Starknet OS `execute_replace_class` diverges from Blockifier's enforcement, enabling honest-node state/proof divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Cairo Starknet OS program's `execute_replace_class` (both current and deprecated syscall tables) unconditionally overwrites a contract's `class_hash` in `contract_state_changes` without checking that the target `class_hash` was ever declared, while the Rust blockifier — which is what actually produces the canonical block/state-diff during sequencing — enforces this declared-class check and rejects (reverts) such calls.

### Finding Description
This mirrors the analog bug class in the report: a critical authorization/precondition check ("is this action permitted against this specific resource?") is enforced in one code path but omitted in a companion path that is supposed to independently reproduce the same result. In Mattermost, the remote-cluster's authority over a specific channel wasn't re-validated before a removal action was applied. Here, the class hash's "declared" status — which gates whether a contract is permitted to assume that identity — is not re-validated by the OS before the state mutation is applied, even though the Rust execution engine treats this as a mandatory precondition.

Concretely:
- Rust blockifier `replace_class` syscall handler explicitly calls `state.get_compiled_class(request.class_hash)?` and fails if the class is undeclared. [2](#0-1) 
- The Cairo OS's `execute_replace_class` performs no such lookup/validation against `contract_class_changes`/declared classes before applying the update, both in the "deprecated" syscalls file and the current syscalls file. [4](#0-3) [5](#0-4) 

### Impact Explanation
If the OS's independently-computed execution trace for a `replace_class` call ever needs to be authoritative (e.g., in `Echonet: OS Validation and Resync Service`, or any path where the OS re-derives state changes rather than merely replaying blockifier's already-validated diff), this control-flow asymmetry can let the OS accept a class-hash write that the sequencer's blockifier would have rejected, producing a state diff / state root that differs from the one the blockifier/consensus computed for the same transaction — i.e., honest-node divergence or a wrong committed root, one of the explicitly accepted impact categories.

### Likelihood Explanation
Exploitability depends on whether the OS's revert/no-revert determination for the enclosing transaction (driven by the `IsReverted` hint) is always sourced from the blockifier's own (correct) result, in which case the OS would simply skip re-executing a reverted call and no divergence manifests. I was not able to fully verify, within the available context, whether *every* invocation path of the OS (including the "OS Validation and Resync Service" mentioned in the wiki) always trusts blockifier-supplied revert hints rather than independently deciding transaction success based on Cairo-only logic. This is a genuine gap in my analysis — the actual reachability of the divergence from a single unprivileged transaction depends on that trust boundary, which I could not confirm with certainty from the retrieved code.

### Recommendation
Add the same declared-class validation to `execute_replace_class` in both `deprecated_execute_syscalls.cairo` and `syscall_impls.cairo` that the Rust blockifier performs — i.e., verify `class_hash` exists in `contract_class_changes` (or the equivalent declared-classes commitment) before permitting the state-entry class-hash swap, and propagate the failure the same way Blockifier's `get_compiled_class` error does.

### Proof of Concept
Not independently reproducible from static analysis alone: constructing a concrete PoC requires confirming whether the OS's revert determination for a `replace_class`-calling transaction is derived independently of the blockifier's own execution result in the specific re-execution/proving pipeline configuration. I could not verify this trust relationship with certainty using the available tools; a background engineering investigation into `apollo_starknet_os_program`'s hint sourcing (`IsReverted`) versus the Echonet OS Validation/Resync service would be needed to confirm concrete reachability of the divergence from a single submitted transaction.

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
