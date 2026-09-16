### Title
Starknet OS `execute_replace_class` syscall omits declared-class-hash validation performed by Blockifier — divergence with unvalidated ID - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The reported bug class is: an unvalidated, attacker-supplied identifier (`loanID`) is used to look up state without checking it actually exists, causing divergent/incorrect outcomes. The sequencer analog is the `replace_class` syscall's `class_hash` argument, which any unprivileged Cairo1 contract invocation can supply. Blockifier validates that the class hash is declared (and is a Cairo1 class) before honoring the syscall, but the Starknet OS Cairo implementation of the same syscall performs no such check — a gap the code itself flags with an open TODO.

### Finding Description
In the Rust execution engine (Blockifier), the `replace_class` syscall explicitly reads the target class from state and rejects the call if it is undeclared or not Cairo1: [1](#0-0) 

This mirrors the deprecated (Cairo0) syscall handler, which also enforces declaration by reading the class before updating state: [2](#0-1) 

However, the Starknet OS's own Cairo implementation of the same syscall (`execute_replace_class` in `syscall_impls.cairo`, used for OS-based re-execution/proving of blocks) takes the `class_hash` from the syscall request and directly writes it into `contract_state_changes` without ever checking that a declared contract class exists for that hash — the omission is explicitly called out by the developers in a TODO comment: [3](#0-2) 

Specifically: [4](#0-3) 

The same class of gap exists in the legacy/deprecated Cairo0 OS syscall handler as well, which likewise sets the new `StateEntry` without any declared-class check: [5](#0-4) 

This is a direct analog of the "invalid ID passed without existence validation" bug class: like `loanID` in the external report, the `class_hash` here is a user-controlled identifier that one implementation (Blockifier) validates against state and the other (the Starknet OS, which re-executes the same transactions to prove the block) does not.

### Impact Explanation
The Starknet OS's job is to independently re-execute every transaction in a block (as run by the sequencer/Blockifier) and produce a STARK proof attesting that the resulting state diff matches what the sequencer actually committed. If a contract invokes `replace_class` with an undeclared (or Cairo0) class hash:
- Blockifier reverts the transaction (an `UndeclaredClassHash` / `ForbiddenClassReplacement` error), leaving the class hash unchanged in the state actually committed by the sequencer.
- The OS, lacking this check, unconditionally updates `contract_state_changes` to the attacker-supplied (possibly nonexistent) class hash and logs it in the revert log as a normal (non-reverting) state change.

This creates an honest-node divergence between the state transition Blockifier actually commits to the chain and the state transition the OS proves as "correct." Such a mismatch undermines the soundness guarantee that the OS-generated proof faithfully attests to the sequencer's committed state — the proof could describe a state root/commitment that differs from (or is inconsistent with) what Blockifier actually produced for the block, which can result in a wrongly proven/committed state root or a block that cannot be consistently validated between the two independently-maintained execution engines.

### Likelihood Explanation
The `replace_class` syscall is reachable by any account or contract executing a normal `INVOKE` transaction — no privileged role, operator, or network condition is required. Supplying an arbitrary, undeclared `class_hash` as the syscall argument is trivial and entirely under the control of a single unprivileged transaction sender.

### Recommendation
Add the missing declared-class-hash check to `execute_replace_class` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo` (and the deprecated Cairo0 handler in `deprecated_execute_syscalls.cairo`) to match Blockifier's `replace_class` behavior in `crates/blockifier/src/execution/syscalls/syscall_base.rs`: verify a compiled class exists for `class_hash` (and, for parity, that it is a Cairo1 class) before updating `contract_state_changes`, reverting/failing the syscall exactly as Blockifier does when the check fails.

### Proof of Concept
1. Deploy a Cairo1 contract exposing a function that calls `replace_class_syscall(class_hash)` with a caller-supplied `class_hash`.
2. Send an `INVOKE` transaction calling this function with a `class_hash` that has never been declared on-chain.
3. Observe that Blockifier reverts the transaction (`state.get_compiled_class` returns `StateError::UndeclaredClassHash`), per the check in [1](#0-0) , and the test coverage confirming this behavior: [6](#0-5) .
4. Trace the same transaction through the Starknet OS `execute_replace_class` path in `syscall_impls.cairo`: no equivalent check exists, so the OS accepts the syscall and writes the undeclared `class_hash` into `contract_state_changes` unconditionally, diverging from the outcome Blockifier committed for the same transaction.

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
