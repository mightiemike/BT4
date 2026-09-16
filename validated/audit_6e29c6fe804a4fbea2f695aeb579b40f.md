Confirmed divergence: the Rust `blockifier` enforces that a class hash passed to the `replace_class` syscall is declared before allowing the state change [1](#0-0) , and the deprecated-syscall path does the same [2](#0-1) . But the Cairo implementation of the same syscall inside the Starknet OS explicitly skips this check, with a TODO acknowledging the gap.

### Title
Starknet OS `execute_replace_class` accepts undeclared/arbitrary class hashes, diverging from blockifier and enabling wrong committed state root - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The report's bug class — trusting a caller-supplied identifier (a "product"/class) without verifying it was legitimately created through the canonical registration mechanism — has a direct sequencer analog: the Starknet OS's Cairo re-execution of the `replace_class` syscall never checks that `request.class_hash` corresponds to a declared contract class before overwriting the contract's `class_hash` in `contract_state_changes`.

### Finding Description
In the OS's `execute_replace_class`, the class hash from the syscall request is written straight into the new `StateEntry` with no declaration check, and the TODO comment confirms this is a known omission: [3](#0-2) . The legacy (`deprecated_execute_syscalls.cairo`) implementation has the identical gap [4](#0-3) .

By contrast, the blockifier (which actually builds/executes blocks on the sequencer) requires the class to already be declared: it calls `state.get_compiled_class(class_hash)` first (which errors with `UndeclaredClassHash` if not declared) before calling `set_class_hash_at` — for both the new syscall interface [5](#0-4)  and the deprecated interface [2](#0-1) . This is confirmed by tests explicitly asserting that an undeclared class hash is rejected during blockifier execution: [6](#0-5)  and [7](#0-6) .

The Starknet OS is the component that re-executes the same block to produce the proven/committed state root (per the "Starknet OS re-execution" scope). If a transaction supplies an undeclared/arbitrary `class_hash` to `replace_class`, the blockifier (used to build/validate the block on the live sequencer) would reject the transaction (or revert it) due to `UndeclaredClassHash`, whereas the OS's Cairo re-execution has no such guard and would happily accept it and commit the new (possibly bogus, attacker-chosen, or malformed) class hash into `contract_state_changes`, feeding it into the state commitment/Patricia tree computation.

### Impact Explanation
This is a state-transition divergence between the block-building execution engine (blockifier) and the proving re-execution engine (Starknet OS). Depending on how the surrounding revert/exception handling is wired, this can lead to either: (a) the OS committing a class hash for a contract that was never legitimately declared, corrupting that contract's class-hash entry in the committed state (state root divergence / honest-node divergence between the block as built and the block as proven), or (b) an OS proving/verification failure for otherwise-valid blocks once the sequencer starts allowing this codepath to diverge from blockifier's stricter enforcement. Either outcome maps to "wrong committed root" or "honest-node divergence," which is explicitly in-scope impact.

### Likelihood Explanation
The `replace_class` syscall is reachable by any deployed Cairo1 contract executed by an ordinary transaction sender (invoke/declare then invoke), requiring no special privileges — it is a standard, whitelisted Starknet syscall. Exploiting the gap only requires calling `replace_class` with a `class_hash` that has never been declared (or is otherwise attacker-controlled/arbitrary), which is a single, unprivileged transaction.

### Recommendation
Add an explicit check in the OS's `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`) that the given `class_hash` is present among declared classes (mirroring blockifier's `get_compiled_class`/declaration check) before writing the new `StateEntry`, resolving the outstanding `TODO(Yoni, 1/1/2026)` marker.

### Proof of Concept
1. Deploy a Cairo1 contract exposing a function that calls the `replace_class` syscall with an attacker-supplied `class_hash` argument (e.g., the `test_replace_class` pattern used in blockifier's own tests) [8](#0-7) .
2. Submit an invoke transaction calling this function with `class_hash = 0xdeadbeef` (an undeclared class hash), analogous to the blockifier test setup [9](#0-8) .
3. On the sequencer's block-building path (blockifier), this transaction reverts/fails with `StateError::UndeclaredClassHash`.
4. If block-building tolerance or a differently-configured execution path allows this state change to reach the OS re-execution stage, the OS's `execute_replace_class` will accept the undeclared class hash unconditionally (per the code at lines 900-914 of `syscall_impls.cairo`), writing it into `contract_state_changes` and ultimately into the committed state root — a result the blockifier's own logic would never have permitted, demonstrating the divergence.

**Uncertainty note:** I was not able to trace the full harness enforcing that only blockifier-accepted transactions ever reach the OS's `execute_replace_class` in production; if such an invariant is strictly enforced upstream (e.g., the OS only re-executes transactions that already succeeded in blockifier), the divergence may be latent/unreachable rather than directly exploitable. Confirming this end-to-end guarantee would require deeper tracing of the sequencer-to-OS transaction handoff, which is best done via a live Devin session with full repository access.

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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo0/test_contract.cairo (L319-323)
```text
@external
func test_replace_class{syscall_ptr: felt*}(class_hash: felt) -> () {
    replace_class(class_hash=class_hash);
    return ();
}
```
