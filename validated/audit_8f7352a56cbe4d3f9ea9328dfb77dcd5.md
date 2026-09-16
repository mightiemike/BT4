### Title
Starknet OS `execute_replace_class` skips the declared-class / class-version check enforced by the blockifier, causing sequencer/OS execution divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall handler in the blockifier (the actual sequencer execution engine) enforces two invariants before allowing a contract to switch class: (1) the target class hash must be declared, and (2) a Cairo1 (V1) class cannot be replaced by a Cairo0 (V0) class hash. The equivalent syscall handler inside the Starknet OS Cairo program — which re-executes the very same transactions to produce the STARK proof of the block — explicitly omits both checks, as flagged by its own `TODO` comment. This is structurally the same bug class as the reported Sui issue: a security-critical invariant is enforced on one code path (the "main" verifier/executor) but is silently absent on an alternate path (the OS re-execution path) that is supposed to compute an equivalent state transition, allowing the check to be bypassed there.

### Finding Description
In the blockifier, `replace_class` is implemented with an explicit "ensure declared" check: [1](#0-0) 

and the newer syscall path enforces a `ForbiddenClassReplacement` error preventing a V1 class hash from being swapped for a V0 class hash: [2](#0-1) 

These checks are exercised and confirmed by tests: an undeclared class hash yields `"is not declared"`, and attempting to replace with a Cairo0 hash yields `"Cannot replace V1 class hash with V0 class hash"`: [3](#0-2) [4](#0-3) 

However, the Starknet OS's own Cairo implementation of the same syscall — used during Starknet OS re-execution to build the STARK proof attesting to the block's state transition — performs neither check. It unconditionally writes the requested `class_hash` into `contract_state_changes`, with an explicit acknowledgment that the declared-class check is missing: [5](#0-4) 

The deprecated (Cairo0) OS syscall path has the identical gap: [6](#0-5) 

An unprivileged transaction sender can trigger this by invoking `replace_class_syscall` (reachable via a Cairo1 contract call as in the feature-contract test helper) with an undeclared class hash or a Cairo0 class hash where a Cairo1 class is expected: [7](#0-6) 

### Impact Explanation
The blockifier is the component that actually builds and commits blocks (executes transactions, computes the state diff, and determines success/revert). The Starknet OS is re-executed over the same transactions to produce the STARK proof that the block's state transition is correct. Because the OS's `execute_replace_class` does not replicate the blockifier's validation:

- A transaction that calls `replace_class` with an undeclared or type-incompatible (V0/V1) class hash will fail/revert in the blockifier (no class-hash state change persisted, transaction marked reverted, only fee charged), while the exact same transaction succeeds inside the OS re-execution and mutates `contract_state_changes` with the invalid class hash.
- This produces a divergence between the state transition actually committed by the sequencer and the state transition the OS proves. Since the OS's output is meant to attest to the sequencer's committed root, this divergence undermines the OS's ability to correctly re-execute and prove the block, resulting in either a proof that does not match the actual committed state (wrong committed root / honest-node divergence) or a hard OS re-execution failure that halts proving for that block, preventing the network from confirming new transactions until resolved. It also allows an unauthorized contract-class assignment (to an undeclared class) to be represented as valid inside the OS's view of state, an unauthorized state action from a single unprivileged transaction.

This satisfies the "wrong committed root ... honest-node divergence ... network unable to confirm new transactions" criteria and is reachable purely through Starknet OS re-execution of a single unprivileged, submitted transaction — in scope per the rules.

### Likelihood Explanation
The trigger requires nothing more than a normal contract calling the standard `replace_class_syscall` with an attacker-chosen class hash (undeclared, or of the wrong Cairo version) — a completely unprivileged action reachable from any invoke transaction. No special permissions, timing, or coordination with operators/provers is required; the divergent behavior fires deterministically every time such a call is re-executed by the Starknet OS.

### Recommendation
Port the blockifier's `replace_class` validation into the Starknet OS Cairo implementation:
1. In `execute_replace_class` (`syscall_impls.cairo`) and the deprecated variant (`deprecated_execute_syscalls.cairo`), verify that `class_hash` corresponds to a class that was declared before this point in the block (mirroring `syscall_handler.state.get_compiled_class` / `get_compiled_class`) and abort/fail the syscall equivalently to the blockifier when it is not.
2. Enforce the same V0/V1 compatibility rule (`ForbiddenClassReplacement`) that prevents replacing a V1 class with a V0 class hash.
3. Add differential tests that run identical `replace_class` scenarios (undeclared hash, cross-version hash) through both the blockifier and the Starknet OS hint execution paths and assert identical revert/success behavior, to prevent future path-specific validation drift.

### Proof of Concept
1. Deploy a Cairo1 contract exposing `test_replace_class` (as in `cairo_steps_test_contract.cairo`, which calls `replace_class_syscall`).
2. Submit an invoke transaction calling `test_replace_class(class_hash)` where `class_hash` is never declared on-chain (or is the class hash of a declared Cairo0 class).
3. Observe blockifier behavior: the call fails with `"... is not declared"` (or `"Cannot replace V1 class hash with V0 class hash"`), matching `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs` — the transaction reverts and the contract's class hash is unchanged in the committed state diff.
4. Feed the identical transaction trace into the Starknet OS re-execution path (`execute_replace_class` in `syscall_impls.cairo`). Because the declared-class/version check is absent there (see the `TODO(Yoni, 1/1/2026)` comment), the OS updates `contract_state_changes` with the invalid `class_hash` without reverting — producing a different final state/output than what the blockifier actually committed for that block.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L90-97)
```rust
#[derive(Debug, Error)]
pub enum SyscallExecutionError {
    #[error("Bad syscall_ptr; expected: {expected_ptr:?}, got: {actual_ptr:?}.")]
    BadSyscallPointer { expected_ptr: Relocatable, actual_ptr: Relocatable },
    #[error(transparent)]
    EmitEventError(#[from] EmitEventError),
    #[error("Cannot replace V1 class hash with V0 class hash: {class_hash}.")]
    ForbiddenClassReplacement { class_hash: ClassHash },
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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
