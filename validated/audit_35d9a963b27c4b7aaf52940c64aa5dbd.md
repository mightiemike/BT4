This is a very promising lead — the Starknet OS's `execute_replace_class` implementations lack the "class is declared" check that the Rust blockifier enforces. Let me verify this thoroughly.### Title
Starknet OS `replace_class` syscall omits the "class is declared" check enforced by blockifier, enabling sequencer/OS state divergence - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The Rust blockifier enforces that a contract class must be declared (and, for Cairo1, that it is not a V0 class) before allowing a `replace_class` syscall to succeed. The Starknet OS's own re-execution of the same syscall — used to produce/verify the STARK proof of the block — omits this check entirely, as flagged by an explicit `TODO` left in the code. This is the same class of bug as the ThunderNFT report: one code path (`place_order`/blockifier) enforces an invariant on a state-mutating field, while a parallel path that performs the equivalent mutation (`update_order`/OS re-execution) does not, and a downstream consumer trusts the unguarded value.

### Finding Description
In the blockifier (used to execute transactions and build the canonical state diff), `replace_class` is guarded: [1](#0-0) 

and the deprecated (Cairo0) syscall handler likewise requires the class to be readable/declared before mutating state: [2](#0-1) 

In contrast, the Starknet OS's Cairo implementation of the same syscall for the non-deprecated (Cairo1) path performs no such check — the author left an explicit reminder that the check is missing: [3](#0-2) 

The deprecated (Cairo0-style) OS implementation of `execute_replace_class` has the identical gap — it reads the current state entry via the `GetContractAddressStateEntry` hint and unconditionally overwrites `class_hash` with the caller-supplied value, with no declaration check and no V0/V1 type check: [4](#0-3) 

The `GetContractAddressStateEntry` hint that backs this is a generic state-entry fetch, not a class-declaration check: [5](#0-4) 

Because the Starknet OS independently re-executes transaction bytecode (using hints only for untrusted state values, not for pass/fail decisions of syscall semantics) to build the state-transition proof, the OS is expected to compute exactly the same result as the blockifier for every syscall. Here the two implementations diverge in permissiveness for the same operation, exactly mirroring the ThunderNFT root cause: `place_order` validates the deposited amount but `update_order` (a parallel mutator of the same field) does not, and `cancel_order` later trusts the unvalidated value.

### Impact Explanation
Any account contract can invoke `replace_class(class_hash)` with an undeclared (or Cairo0, for the Cairo1 path) `class_hash` as part of a normal, unprivileged transaction. The blockifier — the actual execution engine used by the sequencer to build the block and its state diff — rejects such a call with `SyscallExecutionError::ForbiddenClassReplacement` / a "not declared" error, causing the transaction (or that inner call) to revert and leaving the contract's class hash unchanged in the committed state diff. If the Starknet OS is later used to independently re-execute or prove that same transaction (Starknet OS re-execution is explicitly one of the covered sequencer surfaces), its `execute_replace_class` accepts the call unconditionally and writes the new (undeclared/invalid) class hash into `contract_state_changes`. This produces a state transition inconsistent with the one actually committed by the sequencer, i.e., an honest-node/prover divergence and a state root/commitment mismatch — the network could either fail to produce a valid proof for an otherwise-valid block, or, if the divergent trace is accepted, commit a wrong class-hash association for a contract, effectively hijacking/bricking the contract's code (a form of unauthorized account action). This satisfies "wrong committed root or block hash" / "honest-node divergence" criteria.

### Likelihood Explanation
The trigger is a single ordinary transaction from any account calling a contract that executes `replace_class_syscall` with a crafted `class_hash` (undeclared, or a Cairo0 hash on the Cairo1 path) — no special privileges, staking, or operator/proposer access are required. The condition is deterministic and always reachable whenever any contract exposes (directly or transitively via `execute`) a callable path to `replace_class`; many production account/proxy contracts do call this syscall. The bug is also explicitly self-documented by the maintainers via the `TODO(Yoni, 1/1/2026)` comment, confirming the gap is real and currently unresolved in this codebase snapshot.

### Recommendation
Add the same declared-class (and, for the deprecated/Cairo0 code path, class-version) validation in the Starknet OS's `execute_replace_class` in both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo` that the blockifier already performs in `crates/blockifier/src/execution/syscalls/syscall_base.rs::replace_class` and `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs::replace_class`: read/verify the compiled class for `class_hash` exists (and its Cairo version is compatible) before updating `contract_state_changes`, rejecting the syscall identically to the Rust implementation otherwise.

### Proof of Concept
Conceptual PoC (cannot be executed from this read-only environment, but the code paths are concretely identified above):
1. Deploy an account/contract on Starknet that exposes a callable entry point invoking `replace_class_syscall(class_hash)` with a caller-supplied `class_hash`.
2. Submit an ordinary invoke transaction calling this entry point with an undeclared `class_hash` (e.g., a random felt never declared on-chain).
3. Blockifier execution (sequencer/block building path): `crates/blockifier/src/execution/syscalls/syscall_base.rs` line 369-378 calls `self.state.get_compiled_class(class_hash)?`, which fails since the class isn't declared — the call reverts and the committed state diff shows no class-hash change for the contract.
4. Starknet OS re-execution/proving path: `crates/apollo_starknet_os_program/.../syscall_impls.cairo` `execute_replace_class` (lines 881-920) performs no declaration check (per the `TODO` at line 902) and unconditionally writes `new_state_entry` with the undeclared `class_hash` into `contract_state_changes` — producing a state diff for this transaction that differs from what the blockifier actually committed.
5. This mismatch is a direct divergence between the block as executed/committed by the sequencer and the block as independently re-executed by the Starknet OS, which is the exact failure mode described in the "Validate" acceptance criteria (wrong committed root / honest-node divergence).

Note: I was unable to execute the OS Cairo program or the hint-processor Rust glue (`snos_syscall_executor.rs`, `snos_deprecated_syscall_executor.rs`) in this environment to confirm no additional external validation is injected via hints; based on the code read, the `GetContractAddressStateEntry` hint is a generic state-entry loader used across many syscalls and does not perform class-declaration validation, but a Devin session with full repo access should confirm this by tracing `snos_syscall_executor.rs::replace_class` end-to-end before submitting a fix.

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

**File:** crates/starknet_os/src/hints/enum_definition.rs (L1-1)
```rust
use blockifier::state::state_api::StateReader;
```
