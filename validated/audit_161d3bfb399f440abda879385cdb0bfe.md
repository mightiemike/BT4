### Title
Missing "class is declared" check in Starknet OS `execute_replace_class` allows the OS's committed contract-state to diverge from the Blockifier's execution result - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS (Cairo) implementation of the `replace_class` syscall does not verify that the target `class_hash` is a declared class before writing it into `contract_state_changes`, unlike the Rust Blockifier implementation of the same syscall, which explicitly performs this check. This is the same bug class as the Craft CMS report: an authorization/validity check enforced on one code path (`actionDeleteAsset`/Blockifier's `replace_class`) is missing on a structurally-equivalent sibling path (`actionDeleteFolder`/OS's `execute_replace_class`) that performs the same underlying state mutation.

### Finding Description
`AssetsController::actionDeleteFolder()` only enforces `deleteAssets` and skips the `deletePeerAssets` check that its sibling `actionDeleteAsset()` correctly performs, allowing the cascading deletion path to bypass a per-object authorization check enforced everywhere else. The analogous split-brain in this repo is between two implementations of the same Starknet syscall that are supposed to produce identical, consensus-critical results.

The Blockifier's Rust `replace_class` syscall handler enforces that the new class hash must already be declared before allowing the state write: [1](#0-0) 
This check is proven load-bearing by the dedicated test `undeclared_class_hash`, which asserts the transaction is rejected with "is not declared" when the class hash was never declared: [2](#0-1) 

The Starknet OS's Cairo re-implementation of the exact same syscall (`execute_replace_class`, used when the sequencer/prover re-executes the block to produce the OS output and state commitment) omits this check entirely — it is marked with an explicit unresolved `TODO`: [3](#0-2) 

Specifically, line 902 reads `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` and the function immediately proceeds to overwrite the contract's `class_hash` in `contract_state_changes` and record a `RevertLogEntry` without ever consulting `contract_class_changes` or any declared-class dictionary to validate the hash.

Because the Blockifier enforces the check during normal sequencer execution (mempool→batcher→blockifier path) but the OS does not enforce it during re-execution/proving, the two "peer" execution engines that must agree on the resulting state root can diverge if a transaction manages to reach the OS's `execute_replace_class` with a class hash that was not properly declared in the block's `contract_class_changes` (e.g., due to a state-diff/hint inconsistency, a class removed in the same block, or any bug that lets an undeclared-but-nonzero felt reach this hint-fed function). The OS would silently accept this and commit a `StateEntry` referencing a phantom/undeclared class, whereas the Blockifier would have rejected the same transaction — producing honest-node divergence between the sequencer's local execution and the OS-computed state commitment/block hash.

### Impact Explanation
If this divergence is triggerable, it directly matches one of the explicitly-accepted impact categories: "wrong committed root or block hash, honest-node divergence." An OS-computed state root that differs from what the Blockifier would have produced (or from what other conforming nodes compute) breaks the fundamental invariant that the OS is a faithful re-execution of the state transition the Blockifier already validated, corrupting the Patricia-tree-derived commitment used to finalize blocks.

### Likelihood Explanation
The check is provably absent (explicit `TODO`) rather than merely weaker, and the Rust Blockifier's test suite treats the equivalent check as security-relevant (dedicated `undeclared_class_hash` regression test). Whether this is independently exploitable by a single unprivileged transaction sender depends on whether any call path can smuggle an undeclared class hash into `execute_replace_class`'s hinted state entry despite upstream validation elsewhere in the OS pipeline (e.g., declare-transaction bookkeeping, `contract_class_changes` consistency checks) — I could not fully trace whether such an upstream guard exists elsewhere in `os.cairo` or `state.cairo` within the available context, so exploitability should be treated as **unconfirmed** pending further review of `contract_class_changes` validation and the state-diff finalization logic in `crates/apollo_starknet_os_program/.../state/state.cairo`.

### Recommendation
Add the missing declared-class check to `execute_replace_class` in `syscall_impls.cairo`, mirroring the Blockifier's `get_compiled_class` check — i.e., look up `class_hash` in `contract_class_changes` (or the equivalent declared-classes dictionary available to the OS) and fail the syscall/transaction if it is not present, before writing the new `StateEntry`.

### Proof of Concept
Not independently reproducible from the available index: exploitation requires confirming whether any code path can drive `execute_replace_class`'s hinted `class_hash` to an undeclared value without being caught by upstream declared-class bookkeeping in the OS (`contract_class_changes` validation in `state.cairo` / `os.cairo`), which was not fully traceable with the tools available. This should be verified by a Devin session with full repository access before treating this as a confirmed, standalone exploit rather than a code-quality/defense-in-depth gap.

### Citations

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
