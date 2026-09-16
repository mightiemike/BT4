### Title
Missing declared-class check in Starknet OS `execute_replace_class` allows OS/Blockifier divergence on `replace_class` syscall - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Cairo Starknet-OS implementation of the `replace_class` syscall (`execute_replace_class`) unconditionally overwrites a contract's `class_hash` in `contract_state_changes` without verifying that the target `class_hash` corresponds to a declared class. The Rust `blockifier` implementation of the same syscall does perform this check. The Cairo file itself contains an explicit TODO acknowledging the missing check.

### Finding Description
In `execute_replace_class`, the OS reads the requested `class_hash` from the syscall request and immediately builds a new `StateEntry` with that hash, then commits it via `dict_update` into `contract_state_changes`: [1](#0-0) 

The comment directly above this logic states the check is not yet implemented: [2](#0-1) 

By contrast, the Rust blockifier — which is the component that actually decides whether a transaction succeeds/reverts during sequencing — enforces the check by reading the compiled class before allowing the class-hash swap, for both the new syscalls implementation and the deprecated (Cairo0) syscalls implementation: [3](#0-2) [4](#0-3) 

Tests confirm the blockifier's behavior: calling `replace_class` with an undeclared class hash is rejected with an explicit error ("is not declared"): [5](#0-4) [6](#0-5) 

The Starknet OS Cairo program is the authoritative, provable state-transition function whose execution trace is what gets proven and committed on-chain (via the Starknet OS re-execution/proving pipeline). Because its `execute_replace_class` implementation lacks the equivalent declared-class check that the Rust blockifier enforces, the two execution engines can, in principle, disagree on the outcome of a `replace_class` call against an undeclared class hash: the blockifier reverts the call (no state change), while the OS Cairo logic would apply the class-hash overwrite unconditionally.

### Impact Explanation
If the two engines can diverge for the same transaction/calldata, this falls under "wrong committed root or block hash / honest-node divergence" — the class of impact explicitly listed as acceptable in the validation rules. A discrepancy between what the sequencer commits (via blockifier's revert) and what the OS proving program computes (unconditional class-hash overwrite) undermines the guarantee that the proof accurately represents the sequencer's state transition, and could permit an unauthorized change of a contract's class hash to be reflected in the proven/committed state that does not match the state the sequencer itself produced.

### Likelihood Explanation
Likelihood of a genuinely divergent trace occurring depends on whether the OS is always fed transaction traces/calldata that have already been filtered by blockifier's execution (in which case a reverted call would never reach the OS as a "successful" state change) versus whether the OS independently reconstructs execution decisions from raw calldata. I was not able to fully verify, from the indexed portions of the repo, whether upstream OS scaffolding (e.g., the hint `GetContractAddressStateEntry`, or callers of `execute_replace_class`) applies any additional guard before reaching this function that would neutralize the missing check. The explicit `TODO(Yoni, 1/1/2026)` comment in the source strongly suggests the sequencer/OS team is aware this check is not yet implemented and intends to add it, which corroborates that this is a real, currently-unaddressed gap rather than a false positive, but I could not confirm from index-only access whether a compensating check exists at a higher call layer.

### Recommendation
Add the same "class must be declared" check in `execute_replace_class` (and its deprecated-syscall counterpart in `deprecated_execute_syscalls.cairo`, if it has the same gap) that the Rust blockifier performs, i.e., verify `class_hash` corresponds to a declared/compiled class in `contract_state_changes`/class-hash storage before performing the `dict_update`, mirroring `syscall_base.rs::replace_class` and `hint_processor.rs::replace_class`. This keeps the OS's provable execution semantics in lock-step with the blockifier's semantics, closing any potential divergence window.

### Proof of Concept
Not independently reproducible from static code inspection alone. The reasoning is code-based:
1. A contract invokes the `replace_class` syscall with an arbitrary, undeclared `class_hash` (fully attacker-controlled calldata, reachable by any unprivileged sender via a normal invoke transaction).
2. In blockifier (Rust), `SyscallHintProcessor::replace_class` / `DeprecatedSyscallHintProcessor::replace_class` call `state.get_compiled_class(class_hash)` first; for an undeclared hash this returns `Err(StateError::UndeclaredClassHash(_))`, and the call reverts — no class-hash change is applied. [3](#0-2) 
3. In the Starknet OS Cairo program, `execute_replace_class` performs no equivalent check (see the TODO) and unconditionally writes the new `StateEntry` with the attacker-supplied `class_hash` into `contract_state_changes`. [1](#0-0) 

I could not execute the OS program or blockifier in this environment to directly demonstrate a produced state-root mismatch; this PoC outline documents the exact code paths that would need to be exercised (e.g., via `starknet_os_flow_tests`) to confirm whether an actual divergence manifests in an end-to-end run.

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
