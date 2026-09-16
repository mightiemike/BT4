## Analog Found: Missing Declared-Class Validation in Starknet OS `replace_class` Syscall Causes Blockifier/OS State Divergence

### Title
Starknet OS `execute_replace_class` Omits Declared-Class Check Present in Blockifier, Enabling Sequencer/Prover State Divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Rallly CVE stems from a missing authorization/state-validity check tied to a user-supplied identifier (`pollId`), letting an unprivileged caller force an object into a state it should never be able to reach. The closest reachable analog in this sequencer is the `replace_class` syscall: the Rust `blockifier` execution engine enforces that the target `class_hash` is declared before rewriting a contract's class, but the Cairo Starknet OS implementation that re-executes the same transactions for proving purposes has that exact check missing — explicitly marked with a TODO.

### Finding Description
In the blockifier (both the deprecated VM syscall path and the current syscall path), `replace_class` reads the compiled class before writing it, causing an `UndeclaredClassHash` error if the class was never declared: [1](#0-0) 
This is verified by the test suite for both the deprecated and current syscall handlers, which assert an `"is not declared"` error for undeclared class hashes: [2](#0-1) 

However, the Cairo implementation of the same syscall inside the Starknet OS program (used for re-execution/proving of the block) unconditionally writes the new class hash into `contract_state_changes` without ever checking that the class is declared — the missing check is called out directly in the code: [3](#0-2) 
The same gap exists in the deprecated syscall path of the OS program: [4](#0-3) 

The root cause mirrors the Rallly bug class: a state-changing operation keyed on an externally supplied identifier (`class_hash` here, `pollId` there) is executed without validating that the identifier refers to a properly finalized/authorized object (a declared class here, an "unfinalized" ownership check there), even though the equivalent guard exists and is enforced elsewhere in the system (blockifier).

### Impact Explanation
Since the Starknet OS is the component whose Cairo trace is proven and verified on L1 to attest to the correctness of a block's state transition, any divergence between what the blockifier computes (the sequencer's canonical execution, which reverts/rejects `replace_class` calls with an undeclared class hash) and what the OS computes (which accepts the same call unconditionally) breaks the invariant that the OS re-execution matches the sequencer's committed state diff. This can result in either: (a) the OS accepting and committing a class-hash write that the sequencer's blockifier would have rejected/reverted, producing a state root that does not match actual protocol semantics, or (b) an inability to generate a valid/consistent proof for blocks containing such transactions, since OS execution and blockifier execution take different code paths at the same syscall. This falls under "wrong committed root" / "honest-node divergence" between the two execution engines that are supposed to be equivalent.

### Likelihood Explanation
Any account can trigger this by submitting a normal `INVOKE` transaction to a contract that calls the `replace_class` syscall with an arbitrary (undeclared) class hash — no special privileges are required, and the code path is reachable from ordinary contract execution. The blockifier's own test suite confirms this syscall is trivially callable with attacker-controlled undeclared class hashes.

### Recommendation
Add the same "class must be declared" validation to `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` in the Starknet OS program, mirroring the check already performed in `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs` (`state.get_compiled_class(request.class_hash)?` before the state write), so that the OS trace fails/reverts under the same conditions as the blockifier.

### Proof of Concept
1. Deploy a contract exposing an entry point that calls `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared on-chain (as done in `blockifier/src/execution/syscalls/syscall_tests/replace_class.rs::undeclared_class_hash`).
2. Submit an `INVOKE` transaction from any funded account calling that entry point.
3. Blockifier execution (sequencer) rejects/reverts the syscall with `UndeclaredClassHash`/"is not declared".
4. Re-executing the same transaction through the Starknet OS Cairo program (`execute_replace_class` in `syscall_impls.cairo`) proceeds without error, silently updating `contract_state_changes` for the contract to the undeclared class hash — a divergent outcome from step 3.

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
