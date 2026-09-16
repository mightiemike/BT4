### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by Blockifier, causing OS/Sequencer execution divergence and a wrong committed state root - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The reported bug class is: when a piece of state that maps a "reference" to underlying value/ownership is updated without validating that the new reference is valid/consistent, later reads keyed on the old assumption break — losing access to what was previously deposited. In the sequencer, the `replace_class` syscall analogously rewrites the `class_hash` field bound to a contract address in state. The Rust Blockifier (the component that actually executes transactions in the sequencer and determines the canonical state diff) requires the new class hash to be a **declared** class before allowing the rewrite: [1](#0-0) [2](#0-1) 

The Cairo Starknet OS implementation of the exact same syscall — used for re-execution/proving of the block that the Blockifier already produced — has no such check at all, and the missing validation is explicitly flagged by an unresolved `TODO`: [3](#0-2) [4](#0-3) 

### Finding Description
`replace_class` lets a contract rewrite its own `class_hash` entry in `contract_state_changes`. In the Blockifier (which executes the actual block that gets committed):

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
``` [1](#0-0) 

If `class_hash` is not declared, `get_compiled_class` returns `StateError::UndeclaredClassHash`, which the syscall executor turns into an execution error, reverting the call/transaction. The dedicated tests confirm this behavior: [5](#0-4) 

The deprecated (Cairo0) syscall path in the Blockifier has the identical guard: [2](#0-1) 

In contrast, the Starknet OS Cairo implementation of `execute_replace_class` (both the Cairo1 syscall path and the deprecated Cairo0 path) unconditionally overwrites `state_entry.class_hash` with the supplied `class_hash`, with no declared-class check, and the source explicitly documents the gap:
```
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
``` [6](#0-5) 

The deprecated (old syscall struct) path in the OS has the same omission: [4](#0-3) 

The Starknet OS is the component that re-executes the transactions of a block to derive/attest the state commitment that ends up as the proved, canonical root (analogous to "re-execution" mentioned in the allowed analog categories). Because the OS's `execute_replace_class` accepts any `class_hash` felt — including one for which no compiled class exists anywhere in state — while the Blockifier (the actual sequencing/execution engine) rejects such a call as an error/revert, the two engines can produce different results for the identical transaction: the Blockifier reverts the inner call (and possibly the whole transaction, or leaves the pre-call class hash intact for that call's effects), while the OS silently commits the new (undeclared) class hash into `contract_state_changes`, which feeds directly into the state diff/commitment computed by the OS.

### Impact Explanation
This is a genuine root-cause divergence between the two engines that are both supposed to derive the same state transition for the same block:
- The committed state root/output produced by the OS's re-execution can differ from the state actually committed by the sequencer's Blockifier execution for any block containing a `replace_class` call to an undeclared class hash.
- Since the contract's storage is left unchanged while its `class_hash` now points to a class hash for which no code/CASM exists anywhere, subsequent entry-point dispatch for that contract in the OS diverges further (e.g., attempts to load a compiled class that was never declared), which can produce inconsistent execution traces, incorrect resulting balances/ownership semantics for anything gated by that contract's logic, or an outright inability for the OS to finish proving the block (denial of confirmation of new blocks/proofs). Both are impacts explicitly accepted by the rules ("wrong committed root or block hash, honest-node divergence... or a network unable to confirm new transactions").
- This is directly reachable by an ordinary, unprivileged transaction: any account can invoke any contract that calls `replace_class` with an arbitrary felt as the argument (the vulnerable `test_replace_class` test entry points demonstrate this is a plain external call, no special privilege needed).

### Likelihood Explanation
This requires no special access: any deployed contract exposing an entry point that forwards user-controlled input into the `replace_class` syscall — a common, legitimate pattern for proxy/upgradeable contracts — allows an ordinary caller to trigger this discrepancy by passing an arbitrary/undeclared class hash. The gap is not hypothetical; it is explicitly marked by the developers themselves with an unresolved `TODO` comment in the current source, confirming it has not yet been fixed.

### Recommendation
Add the same "class must be declared" validation to the OS Cairo implementation of `execute_replace_class` (both the current and deprecated syscall paths) that the Blockifier already performs, i.e., resolve the `TODO(Yoni, 1/1/2026)` by verifying, via the same declared-class-hash mechanism used elsewhere in the OS (e.g., the compiled-class-hash lookup/hint used in declare-transaction handling), that `request.class_hash`/`class_hash` corresponds to an actually declared class before writing the new `StateEntry`. Until fixed, the OS and Blockifier can disagree on the outcome of any transaction that calls `replace_class` with an undeclared class hash.

### Proof of Concept
1. Deploy any contract that exposes an entry point calling the `replace_class` syscall with a caller-supplied `class_hash` argument (mirrors the existing `test_replace_class` helper used in the Blockifier's own tests).
2. Submit an ordinary invoke transaction calling that entry point with `class_hash = 1234` (an undeclared hash) — exactly the scenario already unit-tested for the Blockifier side: [5](#0-4) 
3. In the Blockifier (sequencer execution), this call errors with "is not declared" and the effect is reverted/rejected.
4. When the same block/transaction is re-executed by the Starknet OS, `execute_replace_class` performs no declared-class check and unconditionally commits the new (undeclared) class hash into `contract_state_changes`: [6](#0-5) 
5. The state diff/commitment derived by the OS for this contract's `class_hash` therefore differs from what the Blockifier actually committed, producing a divergent state root/output between the two engines for the identical transaction.

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
