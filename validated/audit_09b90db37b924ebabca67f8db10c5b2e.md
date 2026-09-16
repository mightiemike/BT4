### Title
Missing declared-class/version check in the Starknet OS's `execute_replace_class` causes state-root divergence from the Blockifier - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall omits the validation that the Blockifier's Rust implementation performs (that the target class hash is declared and is a Cairo 1/V1 class), allowing an OS-vs-Blockifier state divergence.

### Finding Description
Any contract can invoke the `replace_class` syscall to change its own class hash. In the Blockifier (the component that actually executes transactions when building a block), `SyscallHandlerBase::replace_class` explicitly reads the compiled class for the requested `class_hash` (which fails if the class was never declared) and further rejects the call if the class is not Cairo 1 (`ForbiddenClassReplacement`): [1](#0-0) 

In contrast, the Starknet OS's native (non-deprecated) Cairo implementation of the same syscall, `execute_replace_class` in `syscall_impls.cairo`, performs no such check at all — it directly writes the attacker-supplied `class_hash` into `contract_state_changes`, with an explicit TODO acknowledging the missing validation: [2](#0-1) 

Because the Blockifier enforces "class must be declared and must be Cairo1" while the OS enforces nothing, a transaction that calls `replace_class` with an undeclared class hash or with the hash of a Cairo0 (V0) class:
- fails/reverts under Blockifier execution (the class hash the OS ultimately re-derives for the contract differs, or the transaction's success/failure and the resulting `contract_state_changes` differ), while
- succeeds under the OS's re-execution, unconditionally updating the state entry for the contract with the new class hash.

The two components must independently arrive at exactly the same state diff/commitment for the block to be provable and accepted — the OS's job is to prove the same execution the Blockifier performed (see `crates/starknet_os/.../implementation.rs` `assert_transaction_hash` pattern used generally to cross check computed vs. expected values, and `finalize_class_hash`/class hash-preimage assertions elsewhere for declare transactions confirm this "OS must match Blockifier" invariant): [3](#0-2) 

Since `execute_replace_class` skips the declared-class and Cairo-version check that the Blockifier performs, the OS can accept and commit a state transition (a contract's class hash pointing to an undeclared or V0 class) that the Blockifier would have rejected as a revert, or vice versa (the OS commits a class hash the Blockifier never would have committed to). This is a type-confusion-class bug: the OS treats an arbitrary/undeclared class hash pointer as if it were a validated V1 class reference, propagating an unverified type/identity into committed state, analogous to the CVE's engine trusting an object's type without confirming it, leading to state corruption downstream.

### Impact Explanation
This produces one of two critical outcomes accepted by the validation rules:
1. Honest-node divergence / wrong committed state root: the state diff the OS "proves" (accepted as valid) does not match the state diff the Blockifier actually applied when building the block, because the OS's replace_class path admits state transitions the Blockifier rejects (or fails to reject transitions the Blockifier permits only conditionally).
2. Once such a divergent state commitment is accepted by the OS/proof pipeline, the resulting state root committed on-chain no longer reflects the Blockifier's canonical execution, undermining the integrity of the entire state commitment for all contracts, not just the attacker's.

This is reachable by any unprivileged contract deployer/sender by simply invoking `replace_class` with a crafted (undeclared or Cairo0) class hash from within their own contract — no special privileges, staking, or node compromise required.

### Likelihood Explanation
High reachability: `replace_class` is a standard, always-available Starknet syscall invocable by any Cairo1 contract. Triggering the divergent code path requires only calling it with a class hash that is either undeclared or belongs to a Cairo0 contract — both are conditions fully controlled by the calling contract/transaction sender, with no additional preconditions.

### Recommendation
Add the same declared-class and Cairo1-version validation to `execute_replace_class` in `syscall_impls.cairo` (and confirm the deprecated OS syscall path in `deprecated_execute_syscalls.cairo` also matches Blockifier behavior) before writing the new class hash into `contract_state_changes`, mirroring the check already implemented in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class`. Remove the outstanding TODO by implementing the check rather than deferring it, and add a regression/differential test that runs the same `replace_class`-with-undeclared/V0-class-hash transaction through both the Blockifier and the OS re-execution/fuzz harness (`crates/starknet_os_flow_tests/src/fuzz_tests.rs` already references `ReplaceClass` and would be a natural place to add this) to assert their resulting state diffs match.

### Proof of Concept
1. Deploy a Cairo1 contract `A` with an `external` function that calls the `replace_class` syscall with a hardcoded `class_hash` value corresponding to (a) a never-declared class hash, or (b) the class hash of an already-declared Cairo0 (V0) contract.
2. Submit an `INVOKE` transaction from any account calling that function on `A`.
3. Run the transaction through the Blockifier: `SyscallHandlerBase::replace_class` (`crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`) will fail with `UndeclaredClassHash` or `ForbiddenClassReplacement`, causing the call/transaction to revert.
4. Run the same transaction through the Starknet OS's native syscall path (`execute_replace_class` in `syscall_impls.cairo:881-920`): no declared-class or version check exists, so the OS updates `contract_state_changes` for contract `A` with the attacker-supplied class hash unconditionally, producing a state diff/commitment inconsistent with step 3's Blockifier result.

Note: I was not able to execute this PoC end-to-end (no runtime access); the analysis is based on direct comparison of the two implementations' validation logic as shown above. This is worth confirming by running the differential test suggested in the recommendation before treating the severity as fully confirmed at the block-commitment layer.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L734-744)
```text
        // Ensure the given class hash is a result of a Sierra class hash calculation.
        local contract_class_component_hashes: ContractClassComponentHashes*;
        %{ SetComponentHashes %}

        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
    }
```
