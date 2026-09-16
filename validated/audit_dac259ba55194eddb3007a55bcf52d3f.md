This confirms a clear divergence between the blockifier (Rust) and Starknet OS (Cairo) implementations of the `replace_class` syscall.

### Title
Missing Declared-Class Check in Starknet OS `execute_replace_class` Causes Sequencer/OS State Divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Rust `blockifier` enforces that a `replace_class` syscall's target `class_hash` is a declared class before updating a contract's `class_hash` in state, rejecting the transaction with "is not declared" otherwise. The Cairo Starknet OS implementation of the same syscall, used for re-execution and proof generation, contains a `TODO` where this check should be and performs no such validation, unconditionally writing the new (possibly undeclared) `class_hash` into `contract_state_changes`.

### Finding Description
In `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:795-807`, the `replace_class` syscall handler explicitly calls `syscall_handler.state.get_compiled_class(request.class_hash)?` before writing the new class hash — this is the access-control-equivalent guard that ensures only declared classes can be assigned to a contract address, analogous to a missing modifier check in the reported `LibDiamond.sol` bug class. [1](#0-0) 

The corresponding negative-flow tests confirm the sequencer rejects `replace_class` with an undeclared hash with the error "is not declared": [2](#0-1) [3](#0-2) 

However, in the Starknet OS Cairo program's `execute_replace_class` (used for both the deprecated and current syscall paths that the OS uses to re-execute/verify a block for proving), the equivalent check is missing and explicitly marked as an outstanding `TODO`: [4](#0-3) 

The `deprecated_execute_syscalls.cairo` variant has the identical gap — it reads the state entry via the `GetContractAddressStateEntry` hint and unconditionally overwrites `class_hash` with the caller-supplied value, with no check that the class was ever declared: [5](#0-4) 

Since the block was already accepted by the sequencer (which does enforce the check), this specific divergence would not normally trigger with honestly-produced blocks. However, it removes a defense-in-depth invariant the OS is supposed to independently re-verify: the OS is meant to independently validate all state transitions during re-execution rather than trust the sequencer's outputs blindly, per the design in `crates/blockifier_reexecution/src/state_reader.rs` and the OS's re-execution role described in the wiki. If any transaction execution path that constructs the OS-visible transaction trace (e.g., a future/alternate transaction builder, an OS run with hint data that doesn't match sequencer-cached constraints, or a batcher/prover bug that lets an unchecked `class_hash` reach the OS input) supplies an undeclared class hash to this syscall, the OS will silently accept it, compute a state root that includes a contract pointing to an undeclared/non-existent class, and produce a proof/committed state that is inconsistent with what a correct implementation would reject.

### Impact Explanation
The OS is the final validity check before a state root is proven and committed to L1. A missing declared-class check in `execute_replace_class` means the OS can commit a `class_hash` value for a contract without any guarantee that class actually exists/was declared, breaking the invariant that `state.get_compiled_class` (used everywhere else, e.g. `deploy_contract`, `check_and_increment_nonce`) enforces. This can result in a committed state root containing a contract address whose class is unresolvable, which is a form of state corruption/inconsistency that could cause funds tied to that contract to become effectively frozen (calls into that address would fail to resolve a class), and represents an honest-node/prover divergence risk between what the blockifier accepts vs. what the OS independently re-verifies. This aligns with the "wrong committed root" and "honest-node divergence" impact categories.

### Likelihood Explanation
Likelihood is constrained because under normal operation the sequencer's `blockifier` already filters out transactions with undeclared `replace_class` targets before they reach the OS trace, so the current end-to-end pipeline is protected by the blockifier's check. The vulnerability is latent: it is only exploitable if some component feeding the OS (alternate execution engine, reexecution harness, or a future code path) fails to apply the same declared-class check that `blockifier` applies, at which point the OS provides no independent backstop, unlike other state-mutating operations in the same file (e.g. `deploy_contract` explicitly asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH`). Given the explicit `TODO(Yoni, 1/1/2026)` marking this as recognized-but-unresolved, the maintainers themselves flag it as an open gap, increasing confidence that this is a genuine, currently-unpatched omission of a defense-in-depth invariant rather than an intentional design decision.

### Recommendation
Add the same declared-class-hash validation performed in `blockifier`'s `replace_class` handler to the Cairo OS's `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`: before writing the new `state_entry`, assert that `class_hash` exists in `contract_class_changes` (i.e., has been declared), mirroring `state.get_compiled_class(request.class_hash)?` in the Rust implementation, so the OS independently enforces the same invariant rather than relying solely on the sequencer having already checked it.

### Proof of Concept
1. Construct (or directly feed to the Starknet OS Cairo runner) a block trace containing an `INVOKE` transaction whose account executes a `replace_class` syscall with `class_hash` set to a value that was never declared in `contract_class_changes`.
2. Run the block through the Starknet OS (`execute_replace_class` in `syscall_impls.cairo` or `deprecated_execute_syscalls.cairo`) instead of through the `blockifier`'s `replace_class` handler.
3. Observe that the OS does not raise an error (no `is not declared` assertion exists in the Cairo path, unlike `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs:800-801`), and `dict_update` on `contract_state_changes` succeeds, producing a `SquashedOsStateUpdate` and a final committed state root that includes the contract pointing at the undeclared class hash — a state the Rust `blockifier` would have rejected via `assert!(error.contains("is not declared"))` as shown in `crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs:390-391`.

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
