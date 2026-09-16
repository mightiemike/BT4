## Title
Starknet OS `replace_class` syscall omits the declared-class check enforced by Blockifier, causing state root/commitment divergence — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall (both the current and deprecated variants) writes the caller's new class hash into `contract_state_changes` unconditionally, without verifying that the requested `class_hash` corresponds to a declared class. The reference execution engine, Blockifier, enforces this check and reverts the transaction if the class is not declared. This is the same bug class as the reported IDOR: a state-mutating operation accepts a caller-supplied identifier (`class_hash`) and links/writes it into another entity's record (the contract's `StateEntry`) without verifying its validity/existence first.

### Finding Description
In `syscall_impls.cairo`, `execute_replace_class` reads the request's `class_hash` and immediately updates the contract's `StateEntry` with it, with an explicit acknowledgment that the missing check is a known gap: [1](#0-0) 

The deprecated (Cairo0) syscall path has the identical gap with no check and no TODO marker at all: [2](#0-1) 

By contrast, Blockifier's syscall handler explicitly reads the compiled class first (which fails with "is not declared" if the class hash was never declared) before writing the new class hash to state, for both the deprecated: [3](#0-2) 

and current syscall implementations, confirmed by dedicated tests asserting the "is not declared" revert: [4](#0-3) [5](#0-4) 

**Root cause:** the OS's Cairo syscall implementation of `replace_class` is missing the same existence/validity precondition that Blockifier enforces on the `class_hash` argument before writing it into the contract's state entry. The `class_hash` is fully attacker-controlled calldata reaching this syscall from any contract, analogous to the report's unchecked `depends_on_issue_id`.

### Impact Explanation
Block building/execution (Blockifier) and proof generation/re-execution (Starknet OS) must compute an identical state transition for a given block of transactions; the OS's output (state root, block hash commitments) is what gets proven and ultimately verified/settled. If a contract invokes `replace_class` with an undeclared class hash:
- Blockifier reverts the call (transaction is included as REVERTED, class hash unchanged in the state diff that the sequencer commits via the Patricia trie/committer).
- The OS, replaying the same transaction while recomputing the state trace for proving, has no such check and would accept the class-hash write, producing a different resulting state (and thus a different computed state root/commitment) for the same input transaction.

This is a Blockifier/OS execution divergence reachable by any contract making a single `replace_class` syscall call with an arbitrary (undeclared) class hash — exactly the class of "honest-node divergence / wrong committed root" impact this review is scoped to accept.

### Likelihood Explanation
The precondition is trivial: any account can deploy or invoke a contract that calls `replace_class(class_hash)` with an arbitrary, never-declared `class_hash` felt. No special privileges, ordering, or race conditions are required — it is a single-transaction primitive, fully within reach of an unprivileged transaction sender.

### Recommendation
Add the same declared-class existence check to the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that Blockifier performs — i.e., verify the requested `class_hash` is present in `declared_classes` and revert/fail the syscall identically before applying the `StateEntry` update, matching Blockifier's "is not declared" rejection semantics exactly (including the same accepted/rejected class-hash version rules, e.g., "Cannot replace V1 class hash with V0 class hash").

### Proof of Concept
1. Attacker deploys a Cairo1 (or Cairo0) contract exposing a function that calls the `replace_class` syscall with a `class_hash` felt that has never been declared on-chain (e.g., `felt!(1234)`).
2. Attacker submits an invoke transaction calling that function.
3. During block building, Blockifier's `execute_replace_class`/`replace_class` handler calls `get_compiled_class(class_hash)` first, which errors with `UndeclaredClassHash`, causing the transaction to revert; Blockifier's committed state diff shows no class-hash change for the contract (test-proven behavior in `deprecated_syscalls_test.rs::test_replace_class` and `syscall_tests/replace_class.rs::undeclared_class_hash`).
4. During OS re-execution/proving of the same block, `execute_replace_class` in `syscall_impls.cairo` (or the deprecated variant) performs the `dict_update` unconditionally, writing `class_hash=1234` into `contract_state_changes` for the contract with no revert — diverging from the state actually committed by Blockifier.
5. The OS-computed state commitment for this block will not match the state commitment computed by the sequencer's Blockifier-based committer, producing a proof/commitment mismatch (honest-node divergence / wrong committed root) for a state transition that a single unprivileged transaction sender can trigger deterministically.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-914)
```text
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
