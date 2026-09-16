### Title
Starknet OS `execute_replace_class` blindly trusts the class hash without validating declaration or class version, diverging from blockifier's enforcement - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Cairo implementation of the `replace_class` syscall inside the Starknet OS unconditionally accepts any `class_hash` supplied by a contract and writes it into `contract_state_changes`, without checking that the class was actually declared, and without checking its Cairo version. The Rust `blockifier`, which is what the sequencer actually uses to execute transactions and decide success/revert, performs both checks. This produces an execution-semantics gap between "what the sequencer decided happened" and "what the OS proves happened," analogous to the reported issue of blindly trusting an unvalidated/upgraded external input.

### Finding Description
`replace_class_syscall` lets a contract change its own class hash. In the sequencer's blockifier, this is implemented in `SyscallHandlerBase::replace_class`: [1](#0-0) 

This explicitly reads the compiled class (`self.state.get_compiled_class(class_hash)?`), which fails if the class was never declared, and additionally rejects the replacement with `SyscallExecutionError::ForbiddenClassReplacement` if the target class is not Cairo1 (`is_cairo1`).

The Starknet OS (the Cairo program that re-executes the block to compute/prove the committed state) implements the same syscall in `execute_replace_class`: [2](#0-1) 

Note the explicit TODO at line 902: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` No such check, and no Cairo-version check equivalent to `is_cairo1`, exists anywhere in this function. The deprecated Cairo0 execution path (`execute_replace_class` in `deprecated_execute_syscalls.cairo`, lines 307-329) similarly has no declaration check.

Because the check is missing only on the OS side, the OS will happily accept and apply a class-hash replacement to an undeclared (or wrong-version) class hash, updating `contract_state_changes` for that contract, whereas the same transaction executed by the actual sequencer/blockifier would error out and cause the entry point (and by extension the transaction) to revert, leaving the contract's class hash unchanged in the committed state diff produced by the sequencer.

### Impact Explanation
Any account or contract can trigger this divergence by simply invoking `replace_class_syscall` with an undeclared class hash (or, in the Cairo0 path, any hash) from a transaction it controls — this is directly reachable by an unprivileged transaction sender/contract deployer through ordinary contract execution, satisfying the "reachable from a single submitted transaction" requirement. If the sequencer's blockifier reverts the call (because the class isn't declared or isn't Cairo1) while the OS accepts it and commits the class-hash change, the state diff/state root computed by OS re-execution (used for proof generation and on-chain state commitment) will not match what the sequencer/consensus actually agreed on. This is a state root / committed-state divergence — the class of bug explicitly listed as acceptable impact ("wrong committed root or block hash, honest-node divergence"). It could also be leveraged to force a contract into an undeclared/garbage class hash in the OS-proved state, breaking later calls to that contract and potentially causing block proving/verification failures, i.e., a network unable to confirm new blocks built on top of the affected state.

### Likelihood Explanation
The trigger is trivial: any contract can call `replace_class_syscall(some_undeclared_class_hash)`. No special privileges, timing, or race conditions are required. The blockifier-side test suite (`crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`) explicitly demonstrates that an undeclared class hash is rejected ("is not declared") by blockifier, while the OS Cairo code has no equivalent check (confirmed by the unaddressed TODO and absence of `ForbiddenClassReplacement`/`is_cairo1`-equivalent logic anywhere in the OS Cairo sources). This makes the divergence deterministic and easy to reproduce whenever this code path is exercised, though it requires the OS to actually be exercised for state-root computation over the affected block, which is a normal part of proving every block.

### Recommendation
Add, in `execute_replace_class` in `syscall_impls.cairo` (and the Cairo0 equivalent in `deprecated_execute_syscalls.cairo`), the same validation logic present in blockifier's `SyscallHandlerBase::replace_class`: verify that `class_hash` corresponds to a declared class (mirroring `get_compiled_class`) before writing the new `StateEntry`, and reject/replicate the `ForbiddenClassReplacement` behavior for non-Cairo1 classes, so that OS re-execution and blockifier execution agree on revert/success outcomes for this syscall in all cases.

### Proof of Concept
1. Deploy a Cairo1 contract exposing `test_replace_class(class_hash)` (e.g., the existing `TestContract` feature contract used in `crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs`).
2. Submit an invoke transaction calling `test_replace_class` with an arbitrary, never-declared `class_hash` (e.g., `felt!(1234_u16)` as used in the `undeclared_class_hash` test at [3](#0-2) ).
3. Observe blockifier execution: the call fails with "is not declared" and the transaction reverts (no class hash change committed).
4. Run the same block through the Starknet OS re-execution path (`execute_replace_class` in `syscall_impls.cairo`): because the declaration check is absent (line 902 TODO), the OS applies the class-hash replacement to `contract_state_changes` unconditionally and does not flag a revert for this reason, producing a state diff/state root inconsistent with the one committed by the sequencer for the same block.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/replace_class.rs (L17-29)
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
```
