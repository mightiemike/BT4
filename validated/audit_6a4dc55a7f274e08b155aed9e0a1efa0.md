### Title
Starknet OS `execute_replace_class` omits the "class must be declared" check enforced by blockifier, causing OS/sequencer state divergence - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

### Summary
The Starknet OS Cairo program's implementation of the `replace_class` syscall unconditionally rewrites a contract's `class_hash` in `contract_state_changes` without verifying that the target `class_hash` was actually declared. The real sequencer execution engine (`blockifier`), which is authoritative for building blocks, explicitly performs this check and fails/reverts the syscall when the class is undeclared. This creates a "two paths, one checked, one not" bug analogous to the Mattermost advisory (a privileged state-changing action reachable through a code path that skips the authorization/validity check enforced elsewhere), except here the bypassed check is the class-hash declaration requirement, and the divergent path is the Starknet OS re-execution used to produce the STARK proof of the block/state transition.

### Finding Description
`replace_class` is a syscall any contract can invoke to change its own `class_hash`. In blockifier, the shared implementation `SyscallHandlerBase::replace_class` explicitly requires the class to be declared before allowing the write: [1](#0-0) 

This is used by both the VM-based hint processor and the Native syscall handler: [2](#0-1) [3](#0-2) 

The equivalent deprecated (Cairo0) hint processor also enforces the check: [4](#0-3) 

In contrast, the Cairo Starknet OS program — the code that re-executes transactions to produce the STARK proof attesting to the block's state transition — performs no such check. The `TODO` comment explicitly acknowledges the check is missing: [5](#0-4) 

The same gap exists in the deprecated (Cairo0) syscall path of the OS: [6](#0-5) 

Because blockifier rejects (reverts) a `replace_class` call whose target class hash is undeclared — `state.get_compiled_class(class_hash)?` returns `StateError::UndeclaredClassHash` — a transaction calling `replace_class` with an undeclared class hash is included in the block as **reverted** (fee charged, no state change). When the Starknet OS re-executes the same block to build the proof, its `execute_replace_class` has no equivalent check: it unconditionally performs the `dict_update` on `contract_state_changes`, meaning the OS treats the syscall as **succeeding** and commits the contract's class hash change. This produces a state diff/commitment in the OS's proof that differs from the state actually committed by the sequencer/blockifier for the same transaction — an honest-node divergence and a wrong committed root for the affected contract.

### Impact Explanation
This is reachable by a single ordinary transaction (any account or contract can invoke `replace_class` on itself with an arbitrary/undeclared class hash), with no privileged/malicious-operator action needed. The result is:
- Divergence between the state actually maintained by the sequencer (via blockifier, which reverts the call) and the state root/commitment computed by the Starknet OS during proving (which treats it as successful and rewrites the class hash).
- This is either an "honest-node divergence" (two honest components of the same node compute different results for the same transaction) or a "wrong committed root," both explicitly in-scope impact categories. It can corrupt the affected contract's on-chain class binding in the proven state, or cause block-proving/consensus failure once the OS's committed root cannot match the block built by blockifier.

### Likelihood Explanation
High likelihood of reachability: `replace_class` with an arbitrary (including undeclared) class hash is a one-line syscall invocable by any contract from a normal `__execute__` call, requiring no special permissions, and the vulnerable code path (Cairo OS syscall handling) is exercised on every block during proof generation. The missing check is explicitly flagged by an unresolved developer `TODO`, confirming it is a known gap rather than intended defense-in-depth elsewhere.

### Recommendation
Add the same validation performed in blockifier's `SyscallHandlerBase::replace_class` to the Cairo OS's `execute_replace_class` (and the deprecated Cairo0 equivalent): before updating `contract_state_changes`, assert that `class_hash` exists in `contract_class_changes` (i.e., has been declared), mirroring the `assert_not_zero`/dict-read pattern used elsewhere in the OS (e.g., in the bootstrap-declare flow). This keeps the OS's semantics consistent with blockifier so that reverted/successful syscall outcomes match exactly between the sequencer and the proof-generation path.

### Proof of Concept
1. Deploy/own an account or contract exposing the `replace_class_syscall` (e.g., the standard `test_replace_class` external function).
2. Submit an ordinary `invoke` transaction calling `replace_class_syscall(undeclared_class_hash)` where `undeclared_class_hash` is any felt not corresponding to a declared class.
3. In blockifier (actual sequencer execution), `state.get_compiled_class(undeclared_class_hash)` fails, causing the syscall (and thus the transaction) to revert; state is unchanged, fee is charged, and this is what gets committed to the block/state as executed by the sequencer — see the blockifier test demonstrating the negative flow explicitly failing with "is not declared": [7](#0-6) 
4. When the Starknet OS re-executes this same transaction to build the block's validity proof, `execute_replace_class` in `syscall_impls.cairo` performs no declared-class check and unconditionally updates `contract_state_changes`, treating the syscall as successful and producing a state diff where the contract's `class_hash` is changed to the undeclared value — diverging from the state committed by the sequencer for the identical transaction.

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

**File:** crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscalls_test.rs (L375-391)
```rust
#[test]
fn test_replace_class() {
    // Negative flow.
    let chain_info = &ChainInfo::create_for_testing();
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo0);
    let empty_contract = FeatureContract::Empty(CairoVersion::Cairo0);
    let mut state = test_state(chain_info, Fee(0), &[(test_contract, 1), (empty_contract, 1)]);
    let test_address = test_contract.get_instance_address(0);
    // Replace with undeclared class hash.
    let calldata = calldata![felt!(1234_u16)];
    let entry_point_call = CallEntryPoint {
        calldata,
        entry_point_selector: selector_from_name("test_replace_class"),
        ..trivial_external_entry_point_new(test_contract)
    };
    let error = entry_point_call.execute_directly(&mut state).unwrap_err().to_string();
    assert!(error.contains("is not declared"));
```
