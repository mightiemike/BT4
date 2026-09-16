## Analog Found: Missing declared-class and Cairo-version checks in Starknet OS's `execute_replace_class`, unlike the Blockifier's `replace_class`

### Title
Starknet OS `execute_replace_class` omits the declared-class and Cairo-version checks enforced by the Blockifier's `replace_class` syscall - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The external report flags a class of bug where a "canonical"/strict validation function (`pairTransferERC20From`) enforces extra checks that its sibling functions (`pairTransferNFTFrom`, `pairTransferERC1155From`) omit, letting the weaker paths be abused. The same pattern exists between the Blockifier's `replace_class` syscall implementation and the Starknet OS's Cairo re-implementation of the same syscall: the Blockifier enforces that the target class is both declared and of the correct (Cairo 1) version, while the OS's Cairo code performs neither check.

### Finding Description
In the Blockifier, `replace_class` (the "new syscalls" implementation shared by VM and Native execution) is: [1](#0-0) 

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

This enforces two invariants: (1) the class must be declared, and (2) it must be a Cairo 1 (`V1`) class — Cairo 1 contracts are explicitly forbidden from downgrading themselves to a Cairo 0 class, and this is surfaced via `SyscallExecutionError::ForbiddenClassReplacement`.

The Starknet OS's Cairo re-implementation of the same syscall, `execute_replace_class` in `syscall_impls.cairo`, performs neither check: [2](#0-1) 

```
// Replaces the class.
func execute_replace_class{...}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(...);
    ...
}
```

There is no `is_cairo1`-equivalent check and, as the `TODO` comment itself confirms, not even a check that the class hash is declared. The deprecated (Cairo 0) syscall path in `deprecated_execute_syscalls.cairo`'s `execute_replace_class` similarly has no version check, but that mirrors the Blockifier's deprecated-syscalls `replace_class` (which likewise only ensures the class is declared, via `get_compiled_class`) — so that pair is consistent. The divergence is specifically between the Blockifier's *new-syscalls* `replace_class` (used for Cairo 1 accounts/contracts) and the OS's *new-syscalls* `execute_replace_class`, which is missing both checks that its Blockifier counterpart performs.

### Impact Explanation
The Starknet OS re-executes the same block that the Blockifier already executed and committed, in order to produce a STARK proof of the state transition. If a Cairo 1 contract calls `replace_class_syscall` with a class hash that is either undeclared or belongs to a Cairo 0 class:
- In the Blockifier (the actual execution engine used to build/commit the block), the syscall returns an error (`ForbiddenClassReplacement` or a declared-class state error), so the class hash is **not** updated in the real, committed state.
- In the Starknet OS re-execution, the same call is accepted unconditionally, and the OS updates `contract_state_changes` to reflect the class replacement.

This produces two different views of the resulting contract state (and therefore state root) for the same transaction — a direct execution/state divergence between the Blockifier (source of truth for what was committed) and the Starknet OS (source of truth for what gets proven). This can cause the OS-computed state commitment to disagree with the actually-committed state, breaking the correctness guarantee that the OS proof attests to the same state transition the sequencer performed, and undermining confidence in the block's state root/finalization.

### Likelihood Explanation
This is reachable by any account contract deployer/caller: any Cairo 1 contract can invoke `replace_class_syscall` with an attacker-chosen `class_hash` (declared or undeclared, Cairo 0 or Cairo 1) as part of an ordinary transaction — no special privileges are required.

### Recommendation
Add the same two checks to `execute_replace_class` in `syscall_impls.cairo` that exist in `syscall_base.rs::replace_class`:
1. Verify the class hash is declared (resolve/read the compiled class before applying the state update, removing the `TODO`).
2. Verify the class is a Cairo 1 (`V1`) class before allowing the replacement, matching the `ForbiddenClassReplacement` restriction in the Blockifier, and reverting/failing analogously to how the Blockifier surfaces that error to the caller.

### Proof of Concept
1. Declare a Cairo 1 contract `A` with a `replace_class_syscall` wrapper (e.g., `test_replace_class`, as already present in `blockifier_test_utils` feature contracts).
2. From within `A`, call `replace_class_syscall(class_hash)` where `class_hash` is either undeclared or the class hash of a Cairo 0 contract.
3. In the Blockifier, this call returns `SyscallExecutionError::ForbiddenClassReplacement` (or a state error for undeclared), and `A`'s class hash is not modified in committed state — confirmed by existing tests such as [3](#0-2) .
4. Feed the identical transaction/block to the Starknet OS's Cairo execution (`execute_replace_class` in `syscall_impls.cairo`); since no equivalent checks exist there, the OS accepts the call and updates `contract_state_changes` for `A`'s class hash — producing a different post-transaction contract state than the Blockifier computed for the same input.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L31-53)
```rust
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
