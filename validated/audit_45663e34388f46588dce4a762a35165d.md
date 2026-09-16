### Title
Starknet OS `execute_replace_class` syscall omits declared-class / Cairo-version checks enforced by Blockifier, enabling honest-node state divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
Analogous to the Craft CMS bug class (an action mutates a target entity without validating that the caller/target satisfies the authorization/precondition checks enforced elsewhere in the system), the Starknet OS's Cairo implementation of the `replace_class` syscall accepts and commits an arbitrary `class_hash` to a contract's state entry without verifying that the class is declared or that it is a Cairo1 (V1) class — checks that the Rust Blockifier *does* enforce for the same syscall.

### Finding Description
In the real execution engine (Blockifier), `replace_class` explicitly validates the target class before mutating state: [1](#0-0) 
This reads the compiled class (erroring with `UndeclaredClassHash` if not declared) and rejects non-Cairo1 classes via `ForbiddenClassReplacement`, confirmed by tests such as `undeclared_class_hash` and `cairo0_class_hash`: [2](#0-1) 

The deprecated syscall path performs the same check: [3](#0-2) 

However, the Starknet OS Cairo re-implementation of this syscall — used for independent re-execution/proving of the block by the Starknet OS — has **no such check**. The code contains an explicit TODO acknowledging the missing validation: [4](#0-3) 
Note line 902: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` — the function proceeds directly to `dict_update` the contract's `class_hash` in `contract_state_changes` with no validation of declaration status or class version.

The deprecated-syscalls Cairo equivalent (`execute_replace_class` in `deprecated_execute_syscalls.cairo`) has the identical gap: [5](#0-4) 

Because the Starknet OS is the ground-truth Cairo program whose trace is proven and whose resulting state diff/commitment is what gets attested on L1, any semantic gap between it and the Blockifier (the reference execution engine that node operators actually run) is a soundness-relevant divergence: a contract call that Blockifier would revert (undeclared/undeclared-class or wrong-version `replace_class`) is instead accepted and committed by the OS trace.

### Impact Explanation
Any account or contract (reachable from a single, unprivileged invoke transaction) can call `replace_class` with an arbitrary, non-existent, or Cairo0 class hash. Under Blockifier, this call fails/reverts (state unaffected beyond fee). Under the Starknet OS's independent Cairo re-execution, the same call succeeds and commits the bogus `class_hash` into `contract_state_changes`, which flows into the state commitment/Patricia tree and ultimately the block's state root. This produces:
- A wrong committed state root relative to what an honest Blockifier-driven sequencer computed, i.e., **honest-node divergence** and a **wrong committed root**.
- Potential unauthorized account action: silently changing a contract's class to an unintended/undeclared value that downstream consumers (indexers, other contracts using `get_class_hash_at`) would treat as valid, since the OS is meant to be the canonical, provably-correct execution definition.

This satisfies the required impact bar (wrong committed root / honest-node divergence) and is reachable purely via a submitted transaction calling an entry point that issues the `replace_class` syscall — no operator/prover/peer privilege required.

### Likelihood Explanation
High reachability: `replace_class` is a standard, unprivileged syscall available to any Cairo1 contract (see `execute_replace_class`/`test_replace_class` usage in feature contracts), and the missing check is unconditional (not gated behind a feature flag) in both the current and deprecated syscall Cairo implementations. The TODO comment in the code itself confirms the gap is known/unresolved as of the indexed snapshot (dated for a future release, 1/1/2026).

Caveat: I was unable to fully verify within tool-call limits whether some later stage of OS execution (e.g., final state-commitment validation, or a global "all referenced class hashes must appear in `contract_class_changes`" check) retroactively catches this omission before the state root is finalized. My searches for such a cross-check (`is_declared`, `assert_contract_class`, a `contract_class_changes` consistency pass) in the OS did not surface a corrective mechanism, but a definitive verdict would require reviewing the full state-commitment/finalization module (`core/os/state/commitment.cairo` and the top-level OS driver) to rule out compensating controls.

### Recommendation
Add the same checks enforced by Blockifier to the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`):
1. Verify the class hash exists in `contract_class_changes` (i.e., is declared) before permitting the state-entry update, mirroring `state.get_compiled_class(class_hash)?`.
2. Verify the declared class is a Cairo1/V1 class (reject V0 class hashes), mirroring `is_cairo1(&compiled_class)` / `ForbiddenClassReplacement`.
3. On failure, follow the same revert semantics Blockifier uses (fail the call, not the whole transaction, consistent with `SyscallExecutionError::Revert` handling) so that OS-computed state diffs match Blockifier-computed state diffs bit-for-bit.
4. Add OS-level Cairo tests exercising undeclared and Cairo0 class hashes analogous to the existing Blockifier `replace_class.rs` tests to lock in the parity.

### Proof of Concept
1. Deploy any Cairo1 contract exposing a wrapper around the `replace_class` syscall (pattern shown in `crates/blockifier_test_utils/.../account_with_dummy_validate.cairo`'s `deploy_contract`, analogous wrapper exists for `replace_class` per `test_replace_class` selector used in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`).
2. From an unprivileged account, submit an `INVOKE` transaction calling that contract's `replace_class`-wrapper with:
   - an undeclared `class_hash` (e.g., `felt!(1234)`), or
   - a validly declared but Cairo0 `class_hash`.
3. Under Blockifier (actual sequencer execution) the call reverts: [6](#0-5) 
4. Under the Starknet OS Cairo re-execution used to build the provable trace, `execute_replace_class` performs the `dict_update` unconditionally: [7](#0-6) 
   resulting in a state diff/commitment that differs from the one Blockifier would have produced for the identical transaction, i.e., an inconsistency between the two execution engines that are both supposed to define "correct" Starknet state transition.

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
