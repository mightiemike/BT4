### Title
Starknet OS `execute_replace_class` omits the undeclared-class-hash check enforced by Blockifier, causing sequencer/OS state divergence — ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Cairo `execute_replace_class` implementation used by the Starknet OS (the trusted re-execution/proving path) does not verify that the target `class_hash` is a declared class before writing it into the contract's state entry. Blockifier — the sequencer's real Rust execution engine that actually admits/executes transactions and builds blocks — performs this exact check and rejects the call when the class is undeclared. A single unprivileged transaction sender who deploys any contract exposing a `replace_class` syscall call can exploit this asymmetry to make the OS accept a state transition that the sequencer rejected, corrupting the proven state root relative to the sequencer's committed state.

### Finding Description
In Blockifier's syscall handler, `replace_class` explicitly reads the compiled class before allowing the write: [1](#0-0) 

This causes a revert/error (`"... is not declared"`) whenever `request.class_hash` has not been declared, as also demonstrated by the test suite: [2](#0-1) 

However, the Starknet OS's own Cairo implementation of the same syscall — used for the canonical/proved re-execution of the block — has **no such check**. The current (non-deprecated) syscall handler explicitly documents the missing validation as an outstanding TODO and proceeds to unconditionally overwrite the contract's `class_hash` in `contract_state_changes`: [3](#0-2) 

The deprecated syscall variant exhibits the identical gap (no declaration check at all, not even a TODO): [4](#0-3) 

Both variants are wired into the OS syscall dispatch tables and are reachable from any executed transaction: [5](#0-4) [6](#0-5) 

Because Blockifier is used for real-time transaction execution/admission in the sequencer while the Starknet OS is the trusted program whose execution is committed to and proven (its output determines the canonical state root and block hash), any code path where the two engines disagree on whether a call succeeds is a direct route to a state/root mismatch: Blockifier will revert the inner call (charging fee but applying no `class_hash` state change for that call), while the OS will silently accept the same call and persist the (undeclared/attacker-chosen) class hash into `contract_state_changes`, which flows into the block's state diff and Patricia tree commitment.

### Impact Explanation
This produces a **wrong committed root and honest-node divergence**: the state diff/state root that the sequencer computes via Blockifier execution differs from the state diff/state root that the Starknet OS computes when re-executing (proving) the exact same block/transaction. This breaks the invariant that the OS's proof attests to the same state transition the sequencer committed to L1/L2, undermining the soundness of the block's proof and potentially permitting an attacker-chosen (unvalidated) class hash to be written into a contract's on-chain class pointer within the proven state — an unauthorized account/contract state mutation not reflected in the sequencer's own bookkeeping.

### Likelihood Explanation
Likelihood is high in terms of reachability: any deployed contract that calls the `replace_class` syscall (a syscall available since early Starknet, exposed to ordinary Cairo0 and Cairo1 contracts) with an arbitrary/undeclared class hash triggers the divergent code paths. No special privileges, declared classes, or protocol-level access are required — only a normal invoke transaction targeting a contract that performs `replace_class(undeclared_hash)`.

### Recommendation
Add the same "class must be declared" validation to both `execute_replace_class` implementations in the Starknet OS Cairo program (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), mirroring the check already performed in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::replace_class` (`state.get_compiled_class(request.class_hash)?`), so that both execution engines reject (or both accept) the exact same set of `replace_class` invocations. Resolve the outstanding TODO at `syscall_impls.cairo:902` accordingly, and add a regression test verifying that OS re-execution reverts identically to Blockifier when the target class hash is undeclared.

### Proof of Concept
1. Deploy a contract exposing an entry point that invokes the `REPLACE_CLASS` syscall with a `class_hash` value that has never been declared on-chain (e.g., `felt!(1234)`, as used in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`).
2. Submit an ordinary invoke transaction calling that entry point.
3. Observe that Blockifier (sequencer execution) reverts the call with `"... is not declared"` (per `hint_processor.rs::replace_class` and the `undeclared_class_hash` test), producing a transaction receipt/state diff with no class-hash change for the target contract.
4. Feed the identical transaction/block into the Starknet OS re-execution (`syscall_impls.cairo::execute_replace_class` / `deprecated_execute_syscalls.cairo::execute_replace_class`): the OS has no declared-class check and unconditionally updates `contract_state_changes` with the undeclared class hash, producing a state diff/state root that differs from the one committed by the sequencer.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L676-688)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(
            contract_address=execution_context.execution_info.contract_address,
            syscall_ptr=cast(syscall_ptr, ReplaceClass*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - ReplaceClass.SIZE,
            syscall_ptr=syscall_ptr + ReplaceClass.SIZE,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L197-205)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
