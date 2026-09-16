## Title
Missing declared-class validation in Starknet OS `execute_replace_class` causes OS/Blockifier execution divergence — ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall lets a contract change its own `class_hash` in state. In the Rust `blockifier` (the engine that actually decides transaction acceptance/rejection and produces the committed state diff during block building/validation), this syscall enforces that the target `class_hash` is declared by first reading its compiled class, which fails if the class was never declared: [1](#0-0) . In the Cairo Starknet OS re-execution code — the code that re-executes the same block to generate the STARK proof over the very state transitions the blockifier already computed — the analogous `execute_replace_class` function performs no such check at all, and is explicitly marked with a TODO acknowledging the gap: [2](#0-1) . The same missing check exists in the deprecated syscall path used for Cairo0 contracts: [3](#0-2) .

### Finding Description
This is analogous to the reported bug class: two independent code paths that are supposed to reach the same outcome (accept/deny a state mutation) validate a critical field differently, and the discrepancy is reachable from an ordinary, unprivileged action — here, any contract invoking `replace_class_syscall(class_hash)` from an ordinary transaction.

- Blockifier's `replace_class` syscall handler calls `state.get_compiled_class(request.class_hash)?` before writing the new class hash, so an undeclared `class_hash` causes the syscall (and depending on context, the transaction) to fail: [1](#0-0) .
- The Starknet OS's Cairo implementation of the same syscall (`execute_replace_class` in `syscall_impls.cairo`) skips this check entirely, unconditionally writing the caller-supplied `class_hash` into `contract_state_changes` via `dict_update`, with the code comment: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`: [4](#0-3) .
- The deprecated (Cairo0) OS syscall handler has the identical gap — it reads the pre-existing state entry and directly swaps in the new `class_hash` without any declared-class validation: [3](#0-2) .

The Starknet OS is the ground-truth re-execution used to generate the proof that must match the state root the sequencer commits from blockifier's execution (see the Starknet OS Internals wiki area). If a transaction invokes `replace_class` with an undeclared `class_hash`, blockifier (used for block building/validation and computing the committed state diff) rejects/reverts it, while the OS (used to prove the same block) accepts it and writes a different resulting contract state (the class hash actually gets swapped) — producing two different state outcomes for the identical transaction and calldata, from two components of the same sequencer pipeline that are required to agree.

### Impact Explanation
This falls into "honest-node divergence" and "wrong committed root or block hash" categories: any node/pipeline stage relying on the OS's Cairo semantics (proof generation/verification, OS flow tests, or any downstream consumer of OS-produced state diffs) can compute a state root inconsistent with the state root blockifier committed for the same block. Since blocks are proven via the OS, and proof generation encodes the OS's (looser) semantics, a discrepancy here can propagate into an incorrect commitment being proven as valid, or block proving failing due to state mismatch against the blockifier-derived commitment — a chain liveness/consensus-safety issue reachable purely from an unprivileged contract call.

### Likelihood Explanation
Trivially reachable: any account or contract can call `replace_class_syscall` with an arbitrary, undeclared `class_hash` in a single ordinary transaction — no special privileges required. The blockifier path is already covered by tests confirming it rejects undeclared class hashes (`is not declared`) [5](#0-4) , but there is no equivalent negative-path coverage shown for the OS Cairo implementation, consistent with the outstanding TODO.

### Recommendation
Add a check in `execute_replace_class` (both the current `syscall_impls.cairo` version and the deprecated `deprecated_execute_syscalls.cairo` version) that the supplied `class_hash` has a corresponding declared/compiled class fact before performing the `dict_update` that swaps the contract's class, mirroring the `get_compiled_class` check already performed in the Rust blockifier's syscall handler, so both execution engines reject the same set of transactions identically.

### Proof of Concept
1. Declare no class with hash `X` (arbitrary, undeclared felt).
2. From any already-deployed contract, invoke `replace_class_syscall(X)`.
3. In blockifier-driven execution (gateway/batcher/mempool path), the syscall handler's `state.get_compiled_class(X)` fails and the syscall/transaction is rejected — see `crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs:376-392` for the equivalent existing negative test producing "is not declared".
4. Feed the identical transaction into the Starknet OS re-execution path (`execute_replace_class` in `syscall_impls.cairo` / `deprecated_execute_syscalls.cairo`): no declared-class check exists, so the syscall succeeds and `contract_state_changes[contract_address].class_hash` is updated to `X`, diverging from blockifier's rejected/unchanged outcome for the same input.

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
