## Title
Missing "class must be declared" check in the Starknet OS `replace_class` syscall handler allows committing an undeclared class hash, diverging from Blockifier-enforced invariants - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The rekt report describes an MEV bot that lost $2M because a swap-manipulating function lacked access control / validation that other equivalent code paths (or the intended design) required. The structural analog in this codebase is a validation gap between two components that are supposed to enforce the *same* invariant for the same operation: the `replace_class` syscall. The Rust Blockifier explicitly requires the target class hash to already be declared (and be a Cairo1 class) before allowing a contract to replace its own class hash, but the Starknet OS's Cairo implementation of the identical syscall performs the class-hash swap unconditionally, with the declared-class check left as an explicit `TODO`, i.e., unimplemented.

### Finding Description
In the Blockifier (execution layer used to build blocks), `replace_class` is guarded: [1](#0-0) 

and the deprecated (Cairo0) syscall path performs the same check by reading the compiled class before writing the new class hash: [2](#0-1) 

Both Rust implementations fail (`get_compiled_class` returns an error) if the class hash requested was never declared, which is confirmed by the test suite expecting `"is not declared"` errors: [3](#0-2) [4](#0-3) 

However, the Cairo implementation of the same syscall inside the Starknet OS program — the component that re-executes/verifies the block and produces the committed state root, i.e., a "single submitted transaction, contract call ... or Starknet OS re-execution" path per scope — performs the class-hash overwrite unconditionally and explicitly marks the check as not-yet-implemented: [5](#0-4) 

Note line 902's comment: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` — the function reads the state entry via the `GetContractAddressStateEntry` hint and unconditionally writes `new_state_entry` with the attacker-supplied `class_hash` into `contract_state_changes`, with no lookup into `compiled_class_facts_bundle` to confirm the class is declared. This is reachable by any contract executing `replace_class_syscall(class_hash)` — any unprivileged contract deployer/caller — as shown in feature-contract test fixtures: [6](#0-5) 

### Impact Explanation
The Starknet OS is the component whose Cairo execution is proven and whose output (state changes, commitments, block hash) becomes the network's committed truth (per the prompt's explicitly in-scope "Starknet OS re-execution" and "state commitment" categories). Because the OS's `execute_replace_class` does not independently verify that the target class hash was actually declared, it will happily commit a `StateEntry` mapping a contract address to an arbitrary, undeclared/nonexistent `class_hash` in `contract_state_changes` if reached with attacker-controlled hints/inputs. This creates a state entry whose `class_hash` does not correspond to any entry in `compiled_class_facts_bundle`, which is an invariant every other part of the OS (e.g., `execute_entry_point`/`deprecated_execute_entry_point`, which `find_element` over `compiled_class_facts_bundle` keyed by `class_hash`) implicitly relies on. This is a broken/missing check on a state-mutating syscall — the same bug class as the rekt report's unprotected swap function — and can lead to a wrong committed root (an undeclared class hash baked into the committed state diff) or to divergence between the Blockifier's produced result (which would reject/revert such a call) and what the OS is capable of accepting, undermining the guarantee that OS re-execution faithfully re-validates the block rather than blindly trusting supplied hints.

### Likelihood Explanation
Likelihood is high for a single class of trigger: `replace_class` is a permissionless syscall available to any Cairo1 contract, requiring only a contract call with an arbitrary `class_hash` argument — no privileged role, special sequencer/operator behavior, or multi-party collusion is required. The only barrier is whatever external harness/hint-generation logic feeds the OS its `GetContractAddressStateEntry` hint values; if that harness does not itself replicate the Blockifier's declared-class check (which is exactly what the missing code and open TODO suggest), the gap is directly exploitable via a normal transaction.

### Recommendation
Implement the same validation in `execute_replace_class` in `syscall_impls.cairo` (and its Cairo0 counterpart in `deprecated_execute_syscalls.cairo` if not already unified) that the Rust Blockifier performs: look up `class_hash` in `compiled_class_facts_bundle` (mirroring `find_element` usage elsewhere, e.g., in `execute_entry_point`) and abort/fail the syscall if the class is not present, before performing the `dict_update` that swaps in the new `StateEntry`. Additionally add a regression test in the OS test suite asserting that `replace_class` to an undeclared class hash fails deterministically, matching the existing Blockifier test expectations (`"is not declared"`).

### Proof of Concept
1. Deploy any Cairo1 contract that calls `replace_class_syscall(class_hash)` with an arbitrary/undeclared `class_hash` (e.g., as exercised by the `ReplaceClass` scenario in `fuzz_revert.cairo`).
2. In the Blockifier execution path, this call reverts because `syscall_base.rs::replace_class` calls `state.get_compiled_class(class_hash)?`, which errors for an undeclared class.
3. Feed equivalent inputs directly to the Starknet OS's `execute_replace_class` (i.e., construct a scenario/hint where `GetContractAddressStateEntry` succeeds and `class_hash` is undeclared): the function in `syscall_impls.cairo` (lines 881–920) performs `dict_update` unconditionally — no assertion equivalent to the Blockifier's declared-class check exists — demonstrating the OS accepts and commits a state transition that the Blockifier's canonical execution logic rejects.

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

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/fuzz_revert.cairo (L195-198)
```text
            Scenario::ReplaceClass => {
                let class_hash: ClassHash = orchestrator.pop_front().try_into().unwrap();
                syscalls::replace_class_syscall(class_hash).unwrap_syscall();
            },
```
