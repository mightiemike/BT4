### Title
`execute_replace_class` in the Starknet OS never checks that the new class hash is declared - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS's implementation of the `replace_class` syscall writes an attacker-supplied `class_hash` into the contract's state entry without verifying that this class hash corresponds to a class that has actually been declared, unlike the blockifier's implementation of the same syscall which explicitly performs this check.

### Finding Description
In `blockifier`, the `replace_class` syscall handler explicitly reads/validates the compiled class before allowing the replacement: [1](#0-0) 

This mirrors the "Ensure the class is declared (by reading it)" invariant, and is also enforced for the Cairo1 native syscall path (tests confirm the error `"is not declared"` is raised for undeclared class hashes): [2](#0-1) 

However, the Starknet OS's own Cairo implementation of the same syscall, `execute_replace_class`, does not perform this check. It directly overwrites the contract's `class_hash` in `contract_state_changes` with the caller-supplied value, and this omission is explicitly acknowledged by a TODO in the code: [3](#0-2) 

The deprecated syscall path in the OS has the exact same gap (no declared-class check before updating the state entry): [4](#0-3) 

This is directly analogous to the reported issue: a caller-controlled parameter (`class_hash`, analogous to `createOracleParams.factory`) is consumed by privileged logic (writing to the contract's class-hash state entry) without validating that it references a legitimate, declared resource.

### Impact Explanation
The Starknet OS is the canonical execution engine used for re-execution/proving of blocks and for computing the committed state root and block hash. It is expected to enforce the exact same semantics as the blockifier that produced the block, since both are supposed to be equivalent state-transition functions over the same set of transactions. Because the OS's `replace_class` path omits the declared-class check that blockifier enforces, the OS accepts (and would happily prove) a state transition that the blockifier would reject with `StateError::UndeclaredClassHash`. This creates a semantic gap between the two independent execution engines for a syscall reachable by any unprivileged contract via a single ordinary transaction, undermining the guarantee that the OS's re-execution faithfully mirrors blockifier's constraints on contract class hashes, and creating a scenario where a wrong class hash can be committed into the state (and, transitively, into the state commitment / Patricia tree) for an address without that class having ever been declared.

### Likelihood Explanation
Any contract that has a bug enabling arbitrary `replace_class_syscall` invocation (a very common self-upgrade pattern in real Starknet contracts, see `test_replace_class` external entry points) can invoke this syscall with any attacker-chosen `class_hash` felt, since the syscall is a standard external-facing syscall reachable from ordinary `INVOKE`/`__execute__` execution, not requiring any privileged/operator access. The only mitigating factor is that the blockifier itself still performs its own check when actually executing the block; the concrete impact of this OS-level gap materializes specifically when the OS's constraints diverge from blockifier's during re-execution/proving of the same transaction trace, which is the exact assurance the OS is meant to provide.

### Recommendation
Add a "declared contract class" check into `execute_replace_class` (and the deprecated variant) in the Starknet OS Cairo code, mirroring the blockifier's `get_compiled_class` lookup before the class hash is written into `contract_state_changes`, removing the outstanding TODO and closing the semantic gap between blockifier and the OS.

### Proof of Concept
1. A contract with a (buggy or intentionally permissive) external entry point calling `replace_class_syscall(class_hash)` with an arbitrary, never-declared `class_hash` value is invoked via a normal `INVOKE` transaction.
2. In blockifier's actual execution, this is rejected because `get_compiled_class` fails to find the class (`StateError::UndeclaredClassHash`), as verified by the `undeclared_class_hash` test: [2](#0-1) 
3. If the same transaction trace/state entry were fed into the Starknet OS's `execute_replace_class` (e.g., during re-execution/proving), the OS would accept the write of the undeclared class hash into `contract_state_changes` unconditionally, since no analogous check exists: [5](#0-4) 
This demonstrates the divergence: the OS's constraint set for this syscall is strictly weaker than blockifier's, violating the equivalence the OS's re-execution is relied upon to guarantee.

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
