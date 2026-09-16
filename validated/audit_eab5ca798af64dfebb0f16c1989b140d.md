## Finding [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The RDMA/irdma bug class is "a special/degenerate object path that bypasses the validation the normal path enforces, letting an operation succeed with invalid parameters that produce a bogus low-level op." The exact analog exists in the Starknet OS's `replace_class` syscall implementation.

### Title
Starknet OS `replace_class` syscall omits declared-class/version validation enforced by Blockifier, causing honest-node/prover divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The real sequencer execution engine (blockifier) enforces two mandatory checks before honoring a `replace_class` syscall: (1) the target class hash must be declared, and (2) a Cairo1 contract cannot replace itself with a Cairo0 class. The Starknet OS Cairo re-execution code that is used to produce the STARK proof of the block implements `execute_replace_class` without either check — the TODO comment explicitly documents the missing validation.

### Finding Description
In blockifier, `SyscallHandlerBase::replace_class` reads the compiled class (which errors if undeclared) and additionally rejects a Cairo1→Cairo0 downgrade: [3](#0-2) 
The deprecated (Cairo0) syscall path enforces the "declared" check similarly: [4](#0-3) 

Both are proven by tests that assert reverts for undeclared/incompatible class hashes: [5](#0-4) 

In contrast, the Starknet OS's Cairo implementation of the same syscall (used during the OS/prover re-execution of the same block) performs neither check — it unconditionally overwrites the contract's `class_hash` in `contract_state_changes` with the caller-supplied felt, with an explicit TODO acknowledging the gap:
```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [6](#0-5) 
The deprecated-syscall OS path has the identical gap (no declaration or version check at all): [2](#0-1) 

This is the same bug class as CVE-2026-68419: a "special"/less-restrictive execution path (the OS's Cairo re-implementation, analogous to irdma's userspace-registered MR fast path) omits a validation that the primary/authoritative path (blockifier, analogous to the normal `reg_mr`/CQP path) enforces, allowing an operation (`replace_class`) to complete with a value (an undeclared or wrong-version class hash) that should have caused a rejection.

### Impact Explanation
Any unprivileged transaction sender can invoke a contract that calls `replace_class` with an undeclared class hash, or (for a Cairo1 contract) with a declared Cairo0 class hash. During normal block building, blockifier reverts the call (and the transaction, if the failure propagates), so the honest sequencer's computed state diff shows no class change. When the same block is later re-executed by the Starknet OS to generate the STARK proof, the OS's unvalidated `execute_replace_class` succeeds and writes the arbitrary/invalid class hash into `contract_state_changes`, which flows into the OS-derived state diff and state commitment. This produces a divergent state root/commitment between the sequencer's actual execution and the OS-proven execution for the identical block and transaction set — an honest-node divergence and a wrong committed state root, which can prevent block proof finalization or, depending on how the divergent output is consumed, corrupt the committed state of an arbitrary contract (assigning it an undeclared/incompatible class hash).

### Likelihood Explanation
Trivial to trigger: a single invoke transaction from any account that calls `replace_class` (directly or via a deployed contract) with a class hash that is not declared (or, for Cairo1 contracts, a declared Cairo0 class hash) reaches this code on every OS re-execution of that block. No special privileges, timing, or race conditions are required — this is a deterministic divergence reachable by any contract deployer/caller.

### Recommendation
Add the missing validation to both OS syscall implementations to mirror blockifier exactly:
- In `execute_replace_class` (`syscall_impls.cairo`) and `execute_replace_class` (`deprecated_execute_syscalls.cairo`), verify the target `class_hash` corresponds to a declared class (consistent with `contract_class_changes`/declared-class tracking used elsewhere in the OS, e.g. in `execute_declare_transaction`), and reject Cairo1→Cairo0 downgrades to match `SyscallExecutionError::ForbiddenClassReplacement` in blockifier.

### Proof of Concept
1. Deploy a Cairo1 contract exposing an entry point that calls the `replace_class` syscall with an attacker-supplied `class_hash` felt (e.g., `1234`, an undeclared value, or a declared Cairo0 class hash).
2. Submit an invoke transaction calling this entry point.
3. Observe blockifier (sequencer execution) reverts the call/transaction (per `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs::undeclared_class_hash` / `cairo0_class_hash`), so the sequencer's computed state diff is unchanged.
4. Feed the same block/transaction to the Starknet OS re-execution; observe `execute_replace_class` in `syscall_impls.cairo` unconditionally accepts the call and writes the bad `class_hash` into `contract_state_changes`, producing a state diff/commitment that differs from the one computed in step 3.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
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
