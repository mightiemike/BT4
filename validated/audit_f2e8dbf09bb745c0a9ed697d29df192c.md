### Title
Starknet OS `execute_replace_class` Omits Declared-Class Validation Enforced by Blockifier, Enabling Sequencer/OS State Divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The bug class described in the external report ("a restriction enforced in one code path is not enforced in a related code path that operates on the same resource, allowing an unauthorized/invalid state transfer") maps to a concrete divergence between the Rust `blockifier` (which the sequencer uses to build blocks) and the Cairo Starknet OS program (which re-executes the same transactions to prove the state transition). Both implement the `replace_class` syscall, which lets a contract change its own `class_hash` in storage — an operation analogous to Factory's "transfer" of an account resource in the report.

### Finding Description
In the Rust blockifier, `replace_class` is guarded by two checks before the class hash is written to state: [1](#0-0) 
1. The target class must be declared (`self.state.get_compiled_class(class_hash)?` returns `StateError::UndeclaredClassHash` otherwise).
2. The target class must be Cairo1 (`is_cairo1`), otherwise `SyscallExecutionError::ForbiddenClassReplacement` is raised.

The deprecated (Cairo0) syscall handler performs the equivalent declared-class check: [2](#0-1) 

In contrast, the Starknet OS Cairo implementation of the same syscall, `execute_replace_class`, contains an explicit unresolved TODO acknowledging the missing check, and unconditionally writes the caller-supplied `class_hash` into `contract_state_changes` without verifying it corresponds to a declared class or a Cairo1 class: [3](#0-2) 

The comment at line 902 states: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` This is the OS-side analog of the Factory contract not checking `accountVersionBlocked` before transferring an account: a validation ("class must exist/be valid") enforced in one execution path (blockifier) is missing in the other path (OS) that is expected to compute the identical state transition for the same transaction.

### Impact Explanation
The Starknet OS re-executes the block's transactions using facts (declared classes, state entries) supplied by the prover/hint layer to produce a STARK proof of the state transition that the sequencer's blockifier already computed and committed. Both engines must agree bit-for-bit on the resulting state diff (including per-contract `class_hash`) for the proof to be sound and for L1/L2 state commitment to be consistent.

Because the OS omits the declared-class and Cairo-version checks that blockifier enforces:
- A `replace_class` syscall with an undeclared or Cairo0 class hash would be **rejected** by blockifier (causing the transaction to revert, i.e., the class-hash change is rolled back and only fee is charged), but would be **silently accepted** by the OS, which unconditionally updates `contract_state_changes` (subject only to later reversion if some other check in the same call context triggers a revert).
- This creates the potential for the OS-computed state diff to diverge from the blockifier-computed state diff for identical transaction inputs — i.e., honest-node/component divergence and a wrong committed state root for the block being proven, since the class hash actually assigned to a contract's account entry differs between what the sequencer (blockifier) recorded and what the prover (OS) would independently derive if it depended on this behavior. This falls squarely within the accepted impact categories: "wrong committed root or block hash" / "honest-node divergence."

### Likelihood Explanation
`replace_class` is a syscall reachable by any contract executing normal `__execute__`/entry-point logic — i.e., by any unprivileged transaction sender who deploys or calls a contract that invokes `replace_class_syscall`. No special privilege is required to trigger the code path; only a class hash value (which can be arbitrary/undeclared/Cairo0) needs to be supplied as the syscall argument. The blockifier-side unit tests (`crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`) explicitly exercise "undeclared class hash" and "Cairo0 class hash" rejection paths, confirming these are real, reachable negative-input scenarios that the Rust engine defends against but that the OS Cairo code, per its own TODO, does not yet defend against.

### Recommendation
Add the missing declared-class (and Cairo1-version) validation to `execute_replace_class` in `syscall_impls.cairo`, mirroring the checks performed in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class` (and the deprecated syscall equivalent), before applying the `contract_state_changes` update and prior to closing out the item tracked by the `TODO(Yoni, 1/1/2026)` comment. Ensure a corresponding negative test (undeclared/Cairo0 class hash via `replace_class`) is added to the Starknet OS test suite (e.g., `starknet_os_flow_tests`) to lock in parity between blockifier and OS behavior for this syscall.

### Proof of Concept
Not independently executable from static analysis alone; the divergence is demonstrated by comparing the two enforcement points:
1. Blockifier's `replace_class` rejects undeclared/Cairo0 hashes: [4](#0-3) 
2. OS's `execute_replace_class` has no such check and applies the state change unconditionally, per its own TODO: [5](#0-4) 

Note: I was unable to trace whether some other hint/prover-side gate (outside the Cairo program itself, e.g., in `starknet_os` Rust hint implementations) independently blocks undeclared class hashes before this Cairo code runs; this would need to be confirmed by a background engineering session with the ability to run the OS test suite to determine whether the divergence is actually exploitable end-to-end or is mitigated elsewhere in the hint/proving pipeline.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L15-53)
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
