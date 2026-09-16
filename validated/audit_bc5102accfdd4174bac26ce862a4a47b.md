### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by the Blockifier's `replace_class` syscall, allowing state-root/OS re-execution divergence - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The `replace_class` syscall has two independent implementations in this sequencer codebase: the Rust Blockifier (used for actual block execution/state-diff production) and the Cairo Starknet OS (used to re-execute/prove the block and derive the committed state root). The Blockifier's implementation requires that the target `class_hash` be a declared class before it lets the syscall succeed, while the Starknet OS's implementation performs the class hash swap unconditionally, with an explicit TODO admitting the missing check. This is directly analogous to the NFTX finding: a function (`swapTo`) that omits a validation check (`allValidNFTs`) performed by its sibling code path (`mintTo`), letting unvalidated data reach the state-changing operation.

### Finding Description
In the Blockifier, the VM syscall handler's `replace_class` explicitly re-reads the class from state to force validation before mutating state: [1](#0-0) 

The same guard is exercised transitively for the new syscall ABI via `syscall_handler.base.replace_class(request.class_hash)`: [2](#0-1) 

and for Cairo Native execution: [3](#0-2) 

In all Blockifier code paths, `base.replace_class(class_hash)` internally calls `state.get_compiled_class(request.class_hash)?` (per the deprecated syscall handler shown above) which returns a `StateError::UndeclaredClassHash` if the class hash was never declared, causing the syscall to fail/be caught or the whole transaction (if uncaught) to revert with no class-hash state change applied.

In contrast, the Starknet OS Cairo implementation of the exact same syscall — used when replaying/proving the block to compute the final committed state — performs the class hash replacement unconditionally, without checking that the class was declared: [4](#0-3) 

Note the explicit acknowledgment of the gap at line 902: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` The legacy/deprecated OS syscall executor path has the identical omission: [5](#0-4) 

Because the OS derives the resulting `contract_state_changes` dict update purely from the hinted `class_hash` argument with no declared-class check, any contract that (a) calls `replace_class` with an undeclared class hash and (b) has code that would normally have this syscall rejected/reverted by the Blockifier, will instead have the OS silently accept and commit the class-hash swap when the block is proven/re-executed. This creates a divergence between what the Blockifier (the actual sequencer execution engine that produces the state diff written to consensus/storage) computed and what the Starknet OS (the component responsible for proving/deriving the committed state root) computes for the same syscall trace.

### Impact Explanation
This maps to "wrong committed root or block hash / honest-node divergence" from the validation criteria: the Blockifier and the Starknet OS are supposed to be semantically equivalent implementations of the same execution rules, since the OS's re-execution is what ultimately gets proven and used to derive/verify the state commitment. If the Blockifier rejects (or the calling contract catches and ignores) a `replace_class` call to an undeclared class hash — leaving the contract's class hash unchanged in the state diff that the sequencer commits — while the OS unconditionally applies the class-hash overwrite when generating/verifying the proof, the OS-derived state root will not match the state actually committed by the sequencer/full nodes. This can be triggered by any account/contract that reaches `replace_class` with attacker-chosen calldata (a single unprivileged Invoke transaction), placing it squarely in the "single submitted transaction" reachable category with no special privileges required.

### Likelihood Explanation
Reaching this code path requires only a standard Cairo 1 contract capable of invoking the `replace_class` syscall with an arbitrary, attacker-supplied (and possibly never-declared) class hash — something readily reachable from ordinary account contracts or arbitrary calldata-controlled logic. No malicious operator/prover/staker collusion is needed; a single crafted transaction is sufficient to create the divergent code path between Blockifier execution and OS replay.

### Recommendation
Add the missing declared-class check to the Starknet OS's `execute_replace_class` (and the deprecated syscall executor's equivalent), mirroring the Blockifier's `state.get_compiled_class(request.class_hash)?` guard, so that undeclared class hashes cause the OS to reject/revert the syscall identically to how the Blockifier does. This keeps the two execution engines semantically consistent and prevents state-root divergence for otherwise-identical transaction traces. The `TODO(Yoni, 1/1/2026)` comment on syscall_impls.cairo:902 should be resolved as part of this fix, along with the analogous comment in `deprecated_execute_syscalls.cairo`.

### Proof of Concept
1. Deploy a Cairo 1 contract that calls the `replace_class_syscall(class_hash)` with a `class_hash` that has never been declared on-chain, and have the contract catch/ignore syscall failure (e.g., via `unwrap_syscall` alternatives or `match` on the `SyscallResult`) so the outer transaction does not revert.
2. In the Blockifier (sequencer execution / normal node), this syscall will fail internally at `state.get_compiled_class(request.class_hash)?` — see: [6](#0-5) 
The contract's own class hash therefore remains unchanged in the produced state diff, and the transaction still succeeds (since the contract caught the error).
3. When the Starknet OS replays this same transaction trace to build/verify the block (e.g., during proving), `execute_replace_class` unconditionally performs `dict_update` to set the contract's class hash to the undeclared value, with no check: [7](#0-6) 
4. The OS-derived `contract_state_changes` for this contract now includes a class-hash change that never actually happened in the committed state diff produced by the Blockifier, producing an inconsistent/incorrect state commitment relative to the actual sequencer-committed state.

Note: I was not able to fully trace how the OS's hinted `GetContractAddressStateEntry` interacts with earlier declared-class bookkeeping (e.g., whether declared classes are cross-checked elsewhere in the OS pipeline before this point), so it is possible additional guards exist upstream that mitigate this in practice; this could not be fully confirmed from the indexed files alone. If a Devin session with full repository access is available, this should be verified by tracing `contract_class_changes`/declared-class hint wiring around the OS's execution pipeline to confirm whether the state-root divergence described here is fully unmitigated.

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

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L685-693)
```rust
    fn replace_class(
        request: ReplaceClassRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<ReplaceClassResponse, Self::Error> {
        syscall_handler.base.replace_class(request.class_hash)?;
        Ok(ReplaceClassResponse {})
    }
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L395-406)
```rust
    fn replace_class(&mut self, class_hash: Felt, remaining_gas: &mut u64) -> SyscallResult<()> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.replace_class.base_syscall_cost(),
            SyscallSelector::ReplaceClass,
        )?;

        self.base
            .replace_class(ClassHash(class_hash))
            .map_err(|err| self.handle_error(remaining_gas, err))?;
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
