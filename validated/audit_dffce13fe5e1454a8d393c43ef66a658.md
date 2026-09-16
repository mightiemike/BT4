## Finding: Starknet OS `execute_replace_class` does not validate that the class hash is declared, unlike the Blockifier's implementation [1](#0-0) 

### Title
Starknet OS `replace_class` syscall accepts unvalidated/undeclared class hashes, diverging from Blockifier execution - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The external report describes a case where a contract-level function (`_redeem`) trusts an attacker-supplied identifier (an oToken address) without validating that it corresponds to a real, registered asset, allowing the attacker to trigger privileged state changes (burn/payout) on a forged object. The analogous sequencer-level pattern is found in the Starknet OS Cairo implementation of the `replace_class` syscall, which accepts an attacker-controlled `class_hash` felt and unconditionally rewrites the calling contract's class pointer in `contract_state_changes`, without verifying that the class hash corresponds to an actually declared class — unlike the Rust Blockifier, which is the component that actually executes/commits blocks.

### Finding Description
In the Blockifier (used by the Batcher to execute and commit transactions into a block), the `replace_class` syscall handler explicitly requires the target class hash to be declared and to be a Cairo1 (V1) class before mutating state: [2](#0-1) 

The deprecated (Cairo0) syscall path likewise requires the class to be declared before rewriting state: [3](#0-2) 

However, the Starknet OS's Cairo implementation of the same syscall — used for re-execution/proof generation over the same block — performs neither check. The code contains an explicit acknowledgement of the missing validation:
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
``` [4](#0-3) 

This is the direct sequencer-level analog of the original bug class: an unprivileged transaction sender can supply an arbitrary/forged identifier (here, a class hash instead of an oToken address) for a state-mutating operation, and the validating component (the Starknet OS, which is what re-executes and effectively attests to block correctness) trusts it without checking it references a real, declared entity — while the actual block-producing component (Blockifier) does perform this check and would reject/revert the same call.

### Impact Explanation
Because the Blockifier rejects a `replace_class` call to an undeclared (or, for the syscalls variant, non-Cairo1) class hash — causing the invoking transaction/call to revert and leaving the contract's class hash unchanged in the committed block state — while the OS's re-execution of the identical transaction data would accept the very same call and mutate `contract_state_changes` for that contract, this produces a genuine execution divergence between the block that was actually built/committed and the state the OS computes when replaying it (e.g., for L1 state commitment or proof generation). This falls squarely into the impact categories explicitly accepted by the rules: "wrong committed root or block hash" and "honest-node divergence" — either the OS proof will not match the on-chain committed state (network unable to confirm/finalize blocks), or, if the divergent OS-side computation feeds into commitment logic, it results in an incorrect state root being committed for a contract's class hash, which in turn can corrupt that contract's future dispatch of `__execute__`/entrypoints (a form of permanent state corruption reachable by a single unprivileged transaction).

### Likelihood Explanation
The path is trivially reachable: any contract can invoke the `replace_class` syscall from ordinary Cairo1 code with a fully attacker-chosen `class_hash` argument, requiring no special privileges, no cooperation from a malicious operator/prover, and no unusual network conditions — it is a single, deterministic transaction submission.

### Recommendation
Add the missing declared-class check (and, for parity with the Blockifier's Cairo1-only restriction) inside `execute_replace_class` in the Starknet OS Cairo sources before the `dict_update` mutating `contract_state_changes`, mirroring the checks already implemented in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class` and `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::replace_class`, so that the OS's execution semantics are guaranteed to match the Blockifier's for every code path, removing the "TODO" gap.

### Proof of Concept
1. An attacker deploys/uses any Cairo1 contract account and issues an `INVOKE` transaction whose execution path calls `replace_class_syscall(class_hash)` with a `class_hash` value that has never been declared on the network (or, alternately, a declared Cairo0/V0 class hash).
2. When the Batcher executes this transaction via the Blockifier, `syscall_base.rs::replace_class` calls `self.state.get_compiled_class(class_hash)?`, which returns `StateError::UndeclaredClassHash` (or the `ForbiddenClassReplacement` error for a V0 target), causing the syscall/transaction to fail and the class hash change to be discarded from the committed block state.
3. When the Starknet OS later re-executes the same block/transaction (e.g., for state commitment or proof generation), `execute_replace_class` in `syscall_impls.cairo` performs no declared-class or version check and unconditionally updates `contract_state_changes` for the contract to the attacker-supplied `class_hash`, producing a different final state than what was actually committed by the Blockifier for that block.

### Citations

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
