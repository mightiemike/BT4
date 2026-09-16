Found a genuine analog. The Starknet OS `execute_replace_class` syscall handler for Cairo1 contracts explicitly defers the check that the new class hash is actually declared, via an explicit `TODO` comment, unlike the deprecated (Cairo0) syscall path.### Title
Starknet OS `execute_replace_class` accepts an undeclared/uninitialized class hash for `REPLACE_CLASS` syscall, diverging from the blockifier's declared-class check - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The reserve-protocol finding is about a mutable field (`name`) that a governance-controlled setter can change, after which a cached derived value (the EIP-712 domain separator) is never recomputed, breaking an invariant that the two must stay consistent. The closest reachable analog in this sequencer codebase is the `REPLACE_CLASS` syscall handling in the Cairo re-execution program (Starknet OS): the OS-side implementation of `execute_replace_class` explicitly skips validating that the target `class_hash` corresponds to an actually-declared class, unlike the Rust blockifier execution path, which does perform this check. This is a genuine cross-component invariant mismatch reachable from an ordinary contract call (`replace_class` syscall) issued by any unprivileged transaction sender.

### Finding Description
The Cairo1/new syscall path `execute_replace_class` in the Starknet OS program contains an explicit TODO acknowledging the missing check: [1](#0-0) 

Specifically:
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
``` [2](#0-1) 

By contrast, the corresponding blockifier (native execution) implementation of the same syscall — which the OS is supposed to re-execute and verify against — enforces that the class is declared before allowing the replace:
```
fn replace_class(
    request: ReplaceClassRequest,
    ...
) -> DeprecatedSyscallResult<ReplaceClassResponse> {
    // Ensure the class is declared (by reading it).
    syscall_handler.state.get_compiled_class(request.class_hash)?;
    syscall_handler
        .state
        .set_class_hash_at(syscall_handler.storage_address, request.class_hash)?;
    ...
}
``` [3](#0-2) 

and blockifier tests confirm that replacing with an undeclared class hash is rejected with `"is not declared"`: [4](#0-3) [5](#0-4) 

This mirrors the domain-separator bug's structure: an invariant ("the cached/committed value must be consistent with the current declared-class state") that one execution path maintains but another, structurally parallel, path (the OS re-execution program used to produce/verify the block's Cairo trace and state commitment) does not maintain. Since the Starknet OS is the component that re-executes transactions to produce the proven state diff and commitment, if it permits a `replace_class` to an undeclared or garbage class hash where the blockifier (used by the sequencer to build the block) would reject the same transaction, the two execution engines diverge on which transactions are valid and what state changes result, analogous to how the Solidity contract's `name` and the cached domain separator can diverge after governance calls `setName`.

### Impact Explanation
If the OS accepts a `replace_class` syscall for a class hash that is not declared (or was reverted/never committed), it will write that undeclared class hash into `contract_state_changes`, which feeds directly into the committed contract state root via `hash_contract_state_changes` / `get_contract_state_hash`: [6](#0-5) 
This can produce a state root/commitment that the blockifier-based sequencer (which does enforce the declared-class check and would reject such a transaction) considers invalid, or produce a wrong committed root/class-hash mapping for the contract, an honest-node divergence and worst case wrong committed root / block hash if the two components disagree on what's a valid state transition — a Medium impact affecting network's ability to agree on state validity.

### Likelihood Explanation
Likelihood is limited by the fact that this code path is inside the Starknet OS's Cairo hints, is gated behind a documented `TODO(Yoni, 1/1/2026)` (suggesting the team is already aware and plans a fix), and the actual behavioral divergence depends on whether upstream gateway/blockifier validation (which does enforce this check per `replace_class` in `hint_processor.rs`) is bypassed elsewhere in the pipeline that feeds the OS. I was not able to fully verify from the available index whether the blockifier's declared-class check is unconditionally applied before any transaction reaches OS re-execution, or whether there exists a path (e.g., replayed/second-hand blocks, different Cairo1 execution mode) where the class-hash to replace can reach the OS without that upstream check. This uncertainty should be resolved with a live Devin session that traces the full execution pipeline from syscall dispatch through to OS input generation.

### Recommendation
Implement the missing check flagged by the `TODO(Yoni, 1/1/2026)` comment in `execute_replace_class` (crates/apollo_starknet_os_program/.../syscall_impls.cairo) to verify that `class_hash` corresponds to a declared, non-reverted contract class before updating `contract_state_changes`, mirroring the check already performed in the Rust blockifier's `replace_class` syscall handler (`syscall_handler.state.get_compiled_class(request.class_hash)?` in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs`), ensuring both execution engines agree on validity of `replace_class` before allowing any commitment computation to proceed.

### Proof of Concept
Not independently reproducible from the static index alone (requires running the full Starknet OS Cairo program against a crafted execution trace), but the divergence is demonstrable by comparing:
1. Blockifier native execution: calling `replace_class_syscall(undeclared_class_hash)` from a contract fails with `"is not declared"` per `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`.
2. Starknet OS re-execution of the same call: `execute_replace_class` in `syscall_impls.cairo:881-920` performs no such check and unconditionally writes the new (possibly undeclared) class hash into `contract_state_changes`, which is later hashed into the committed state root via `commitment.cairo`.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L148-206)
```text
func hash_contract_state_changes{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_address: felt,
    prev_state: StateEntry*,
    new_state: StateEntry*,
    patricia_update_constants: PatriciaUpdateConstants*,
    hashed_state_changes: DictAccess*,
) {
    alloc_locals;

    local initial_contract_state_root;
    local final_contract_state_root;

    %{ SetPreimageForCurrentCommitmentInfo %}

    local state_dict_start: DictAccess* = prev_state.storage_ptr;
    local state_dict_end: DictAccess* = new_state.storage_ptr;
    local n_updates = (state_dict_end - state_dict_start) / DictAccess.SIZE;
    // Call patricia_update_using_update_constants() (or the read-optimized variant) instead of
    // patricia_update() in order not to repeat globals_pow2 calculation.
    local should_use_read_optimized: felt;
    %{ ShouldUseReadOptimizedPatriciaUpdate %}
    if (should_use_read_optimized != 0) {
        patricia_update_read_optimized(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    } else {
        patricia_update_using_update_constants(
            patricia_update_constants=patricia_update_constants,
            update_ptr=state_dict_start,
            n_updates=n_updates,
            height=MERKLE_HEIGHT,
            prev_root=initial_contract_state_root,
            new_root=final_contract_state_root,
        );
    }
    local range_check_ptr = range_check_ptr;

    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;

    return ();
}
```
