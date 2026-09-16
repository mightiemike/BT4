### Title
Missing Declared-Class Validation in Starknet OS `replace_class` Syscall Causes Blockifier/OS State Divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Starknet OS (SNOS) Cairo implementations of the `replace_class` syscall — used to re-execute blocks for proof generation — do not verify that the supplied `class_hash` corresponds to a declared contract class before applying the class-hash change to `contract_state_changes`. In contrast, the actual sequencer execution engine (blockifier, in Rust) explicitly performs this check and rejects the syscall if the class is undeclared or not Cairo1.

### Finding Description
In the blockifier's syscall implementation, `replace_class` first ensures the class is declared by reading it via `get_compiled_class`, and additionally requires it to be a Cairo1 class, erroring out otherwise: [1](#0-0) 

This is confirmed by tests asserting that replacing with an undeclared class hash fails with `"is not declared"`, and that replacing with a Cairo0 class hash also fails: [2](#0-1) 

However, the equivalent syscall handler inside the Starknet OS Cairo program (used for re-execution / STARK proving of the block) performs no such check. The code contains an explicit acknowledgment of the gap: [3](#0-2) 

The same omission exists in the deprecated (Cairo0) syscall path used by the OS: [4](#0-3) 

Both OS implementations unconditionally write the new `class_hash` into `contract_state_changes` regardless of whether that class was ever declared (or whether it's a valid Cairo1 class in the Cairo1 case). This is the same bug class as the reported issue: a critical value supplied to an operation is used without validating that it corresponds to a properly "implemented"/declared entity, which can cause divergent behavior between two code paths that are supposed to agree.

### Impact Explanation
The blockifier is the authoritative execution engine that actually builds blocks and commits state; the Starknet OS independently re-executes the same transactions to produce the STARK proof that must match the state root the sequencer committed. If a transaction invokes `replace_class` with an undeclared (or, for the Cairo1 path, a Cairo0) class hash:
- The blockifier will reject/revert that call (per `syscall_base.rs`), so the class hash of the target contract is **not** changed in the block that the sequencer actually commits.
- The Starknet OS, re-executing the identical transaction, will **not** reject the call and will apply the class-hash change to its own `contract_state_changes`.

This produces two different final states for the same block — one committed by the sequencer, and a different one computed/proved by the OS. This is a "wrong committed root / honest-node divergence" class issue per the escalation criteria: it can prevent the network from producing a valid proof matching the actual committed block (liveness/availability of proving), or in the worst case, allow an attacker-crafted transaction to make the OS "prove" a state transition that never actually happened on-chain (an unauthorized class replacement not reflected in the real state), undermining the correctness guarantee that the validity proof is supposed to provide.

### Likelihood Explanation
This is trivially reachable by any unprivileged transaction sender: any contract that exposes (or is coerced to invoke) the `replace_class` syscall with a felt value that is not a declared class hash triggers the divergence. No special privileges, node compromise, or off-chain conditions are required — a single ordinary INVOKE transaction calling a contract that performs `replace_class_syscall(undeclared_class_hash)` is sufficient to exercise both code paths differently.

### Recommendation
Add the missing declared-class check to both OS `execute_replace_class` implementations (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), mirroring the blockifier's behavior in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class`:
1. Verify the target `class_hash` exists in `contract_class_changes` / declared-class state before allowing the `dict_update` to `contract_state_changes`.
2. For the Cairo1 path, additionally enforce that the class is a Cairo1 (not Cairo0) class, matching the `ForbiddenClassReplacement` restriction in the Rust implementation.
3. On failure, cause the OS to treat the call the same way the blockifier does (revert/failure propagation), so that both execution engines agree on whether the `replace_class` syscall succeeds.

### Proof of Concept
1. Attacker deploys or reuses any contract exposing `replace_class_syscall` (e.g., the `test_contract`'s `test_replace_class` entry point pattern already used in blockifier tests).
2. Attacker submits an INVOKE transaction calling this entry point with a `class_hash` that has never been declared (e.g., an arbitrary felt not present in `contract_class_changes`).
3. Sequencer (blockifier) execution: `replace_class` syscall calls `get_compiled_class(class_hash)`, which returns `StateError::UndeclaredClassHash`, causing the call/transaction to fail — confirmed by the existing test `undeclared_class_hash` in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs:17-29`. The block committed on-chain shows no class-hash change for the target contract.
4. Starknet OS re-execution of the same transaction: `execute_replace_class` in `syscall_impls.cairo`/`deprecated_execute_syscalls.cairo` performs no declared-class check and unconditionally updates `contract_state_changes` with the new (undeclared) class hash, producing a state diff that differs from what the sequencer actually committed.
5. This mismatch between the sequencer-committed state and the OS-derived state constitutes a state root/commitment divergence for the same block and transaction set.

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
