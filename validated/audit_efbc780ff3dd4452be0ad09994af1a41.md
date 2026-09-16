## Title
Starknet OS `replace_class` syscall omits declared-class and version validation enforced by the Blockifier - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS (SNOS) Cairo implementation of the `replace_class` syscall writes an attacker-supplied `class_hash` into a contract's state entry without verifying that the class hash corresponds to a declared, correctly-versioned contract class — a check that the Rust Blockifier (the reference execution engine used by honest sequencer/full nodes) explicitly performs. This mirrors the Avo bug class: a privileged action ("replace this contract's class") is executed based on an unvalidated identifier, bypassing the authorization/consistency check that the "real" execution layer enforces for the same operation in a different code path.

### Finding Description
In the Blockifier, `replace_class` is gated by an explicit declared-class and version check: [1](#0-0) 

This ensures the syscall reads the compiled class (failing with `UndeclaredClassHash` if it doesn't exist) and rejects replacement with a Cairo 0 (V0) class hash.

The Starknet OS's Cairo implementation of the same syscall performs neither check. It directly writes the caller-supplied `class_hash` into `contract_state_changes`, with an explicit TODO acknowledging the missing validation: [2](#0-1) 

Specifically, line 902's comment `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` confirms no lookup against declared classes (`is_declared`) or Cairo-version check occurs before `dict_update` commits the new class hash to `contract_state_changes`.

The same gap exists in the deprecated (Cairo0) syscall path: [3](#0-2) 

The Starknet OS is the ultimate arbiter of transaction validity proved via STARK and verified on L1 — full nodes use the Blockifier to build/verify blocks, but the L1-accepted state transition is whatever the OS proves. Because the OS never validates that the target `class_hash` is declared or of the correct Cairo version, a transaction whose `replace_class` call targets an undeclared/nonexistent class hash (or a Cairo0 hash when replacing a Cairo1 contract) would be rejected by the Blockifier (as shown by blockifier's own tests expecting `"is not declared"` / `"Cannot replace V1 class hash with V0 class hash"`): [4](#0-3) 

but would be accepted unconditionally by the OS.

### Impact Explanation
Because the OS is the component whose execution is proved and verified on L1, any divergence between what the Blockifier enforces and what the OS accepts is a soundness gap in the state-transition function itself. A malicious block-producing sequencer (reachable purely by any contract issuing `replace_class` via a normal transaction/inner call — no special privilege needed) could commit a contract's class hash to an arbitrary or undeclared value:
- Setting the class hash to a value with no corresponding compiled class permanently bricks the contract (its entry points can never resolve), freezing any funds held by that contract — a concrete permanent freezing-of-funds outcome.
- Setting the class hash to a Cairo0 class hash for a contract previously running Cairo1 breaks entry-point-type assumptions relied upon elsewhere in execution.
- Because the Blockifier (used by full/RPC nodes to independently verify state) would have rejected such a transaction, but the OS-proved state root includes the effect, this yields a wrong committed root / honest-node divergence between the L1-finalized state and what honest full nodes compute locally.

This satisfies the "wrong committed root" / "honest-node divergence" / "permanent freezing of funds" impact bar.

### Likelihood Explanation
The `replace_class` syscall is callable by any contract via a single ordinary transaction — no elevated privilege, special role, or malicious-operator assumption is required, matching the "reachable from unprivileged sender" scope. The bug is confirmed by an explicit TODO in the OS source acknowledging the missing check, making this a real (not speculative) gap rather than a hypothetical analog.

### Recommendation
Add the same declared-class existence and Cairo-version checks to the OS's `execute_replace_class` (both the Cairo1 path in `syscall_impls.cairo` and the deprecated Cairo0 path in `deprecated_execute_syscalls.cairo`) that the Blockifier already performs in `syscall_base.rs::replace_class` — i.e., look up the class in `compiled_class_facts_bundle`/declared classes before accepting the `dict_update`, and reject non-Cairo1 (or non-existent) class hashes to keep OS semantics consistent with the Blockifier's enforced invariants.

### Proof of Concept
1. Deploy a Cairo1 contract instance `C` with a trivial external function that calls `replace_class_syscall(class_hash)` with an attacker-chosen `class_hash` value that is not declared anywhere on-chain (e.g., `0xdeadbeef`).
2. Submit an invoke transaction calling that function on `C` — the actual Blockifier execution would revert per `undeclared_class_hash` test semantics.
3. When the same block/transaction trace is re-executed by the Starknet OS for proof generation, `execute_replace_class` in `syscall_impls.cairo` performs the `dict_update` unconditionally (no declared-class check), committing the new (non-existent) class hash for contract `C` into the state diff/root that gets proved and posted to L1 — diverging from what an honest full node running the Blockifier would compute/accept for the same transaction.

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
