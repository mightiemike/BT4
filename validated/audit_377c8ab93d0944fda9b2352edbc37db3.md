### Title
Starknet OS `execute_replace_class` omits the declared-class and Cairo-version checks that `blockifier`'s Rust `replace_class` syscall enforces - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Rust execution engine (`blockifier`) enforces two conditions before a contract's `replace_class` syscall is allowed to change a contract's class hash: (1) the target class hash must be declared, and (2) the target class must be a Cairo 1 (Sierra) class, not a deprecated Cairo 0 class. The Cairo implementation of the same syscall inside the Starknet OS program (used for re-execution / STARK-proof generation) does not perform either check — it is explicitly marked with an unresolved `TODO`.

### Finding Description
`blockifier`'s syscall implementation validates the `replace_class` request before mutating the contract's `StateEntry`: [1](#0-0) 

This reads the compiled class (returns an error if `class_hash` is undeclared) and rejects the replacement if the class is not Cairo 1 (`ForbiddenClassReplacement`), matching the test expectations `"is not declared"` and `"Cannot replace V1 class hash with V0 class hash"`: [2](#0-1) 

The Starknet OS's Cairo re-implementation of the same syscall (`execute_replace_class` in `syscall_impls.cairo`) blindly writes the new `class_hash` into `contract_state_changes` without checking that a declared contract class exists for that hash: [3](#0-2) 

Line 902 in that file contains the literal comment:
```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```
No equivalent check exists in this Cairo function for either "is declared" or "is Cairo 1" — both restrictions that `blockifier` enforces on the same syscall.

### Impact Explanation
The Starknet OS is the authoritative, provable re-execution of the block: its execution trace is what generates the STARK proof that attests to the correctness of the state transition, independent of trust in the sequencer/blockifier binary that originally produced the block. Because the two execution engines that are supposed to compute identical results for the same input transactions implement different validity rules for `replace_class`, they can diverge on inputs where blockifier's rule would reject the transaction (undeclared class hash, or a Cairo 0/deprecated class hash) but the OS's rule would silently accept it and apply the state change. Any divergence between the OS's computed state transition and the actual state transition enforced by the sequencer is exactly the class of bug this scan is scoped to catch (`Starknet OS re-execution`, `honest-node divergence`, `wrong committed root`): if the OS ever executes a code path independently of blockifier's acceptance decision (e.g., different code versions, out-of-sync deployments, or future re-use of this OS function as the sole gatekeeper), the OS would accept and commit a class replacement to an undeclared or Cairo-0 class hash that should have been rejected, producing a state root/proof that does not match protocol rules enforced elsewhere in the stack.

### Likelihood Explanation
Under the current architecture where blockifier is the only component deciding which transactions enter a block, this gap is latent rather than immediately triggerable by an unprivileged transaction sender in isolation, because blockifier's stricter check filters offending transactions before they reach the OS. However, the bug is directly reachable in the source: any account contract can invoke the `replace_class` syscall with an arbitrary, attacker-chosen `class_hash` argument (undeclared or Cairo 0), and the OS's replay of that exact call performs no validation. The developers themselves flag this as an open, dated TODO (`TODO(Yoni, 1/1/2026)`), confirming it is a known, unresolved correctness gap rather than an intentional design decision, and it directly parallels the reported bug class: a state-mutating routine that omits verifying the target key belongs to the correct "active"/"declared" set before mutating shared state.

### Recommendation
Add the missing checks to `execute_replace_class` in `crates/apollo_starknet_os_program/.../execution/syscall_impls.cairo` to mirror `blockifier`'s `syscall_base::replace_class`:
1. Verify `class_hash` corresponds to a declared contract class (equivalent to `state.get_compiled_class(class_hash)?`).
2. Verify the declared class is Cairo 1 (reject Cairo 0/deprecated class hashes), matching the `ForbiddenClassReplacement` behavior in Rust.
Resolve the `TODO(Yoni, 1/1/2026)` before relying on this OS code path as an independent source of truth for block validity.

### Proof of Concept
1. An account contract calls `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared (or that corresponds to a Cairo 0 class).
2. In `blockifier`, `SyscallHintProcessor`/native syscall handler calls `syscall_base::replace_class`, which calls `self.state.get_compiled_class(class_hash)?` — returning `StateError::UndeclaredClassHash` (or `ForbiddenClassReplacement` for a V0 hash) and aborting the syscall, as covered by `replace_class.rs::undeclared_class_hash` and `::cairo0_class_hash` tests.
3. In the Starknet OS Cairo implementation (`execute_replace_class`, `syscall_impls.cairo:881-920`), the same `class_hash` is written directly into `contract_state_changes` with no declared-class or version check — confirmed by the unresolved `TODO(Yoni, 1/1/2026)` comment at line 902.
4. Because the two implementations of the identical protocol syscall enforce different sets of validity constraints, any code path or future scenario in which the OS's decision is authoritative independent of blockifier's prior rejection results in acceptance of a class replacement that should be invalid — a discrepancy directly analogous to the reported `_incrementGaugeWeight` bug (checking one exclusion condition while omitting the corresponding inclusion/membership check before mutating state).

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
