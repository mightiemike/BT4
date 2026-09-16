### Title
Missing "reject in Validate mode" check for `meta_tx_v0` in the Starknet OS re-execution path allows a validate-phase syscall that Blockifier forbids - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
Blockifier's Rust syscall implementation explicitly rejects the `meta_tx_v0` syscall when invoked during the `__validate__` execution phase, but the equivalent syscall implementation used by the Starknet OS re-execution / proving path (both the Cairo OS program and its Rust hint-processor counterpart) does not perform this check. This is structurally the same bug class as CVE-2026-42359: a forbidden/validation check enforced on one code path (Blockifier) but missing on the parallel path that reaches the same operation (Starknet OS).

### Finding Description
In the Blockifier execution engine, `SyscallHandlerBase::meta_tx_v0` explicitly guards against use during `__validate__`: [1](#0-0) 

This mirrors the same pattern used for other validate-restricted syscalls such as `get_class_hash_at`: [2](#0-1) 

The corresponding logic in the Starknet OS re-execution/verification path — used to prove the correctness of a committed block (`starknet_os` crate) — implements `meta_tx_v0` but only validates the entry-point selector, not the execution mode: [3](#0-2) 

Likewise, the underlying Cairo program that the OS proves, `execute_meta_tx_v0`, dispatches the meta-transaction call (computing the meta-tx hash, swapping `tx_info`, and invoking `contract_call_helper`) with no visible restriction tied to whether the caller is executing inside `__validate__`: [4](#0-3) 

The syscall dispatcher `execute_syscalls` in the same OS program routes to `execute_meta_tx_v0` purely by selector, without evidence of an execution-mode gate at the dispatch level: [5](#0-4) 

Because `run_validate` (which executes `__validate__`) uses the same generic `execute_syscalls`/entry-point execution machinery as `__execute__`, an account's `__validate__` entry point could invoke `meta_tx_v0` during the OS's constrained re-execution without triggering a rejection, even though Blockifier — the reference execution engine that actually builds blocks — refuses to execute the same call in the same phase.

This is precisely the "PATCH endpoint not covered by the POST endpoint's validator" pattern from the reported Airflow CVE: two functionally-equivalent entry points into the same sensitive operation (invoking `meta_tx_v0`, which lets a contract mint a synthetic version-0 transaction context and re-enter `__execute__` on an arbitrary target as the OS-caller with attacker-controlled signature/calldata) are checked inconsistently.

### Impact Explanation
`meta_tx_v0` is a sensitive syscall: it lets the caller synthesize a completely different `TransactionInfo` (version 0, zero fee, `caller_address = 0`, attacker-chosen signature) and invoke `__execute__` on an arbitrary target contract. Blockifier forbids this during `__validate__` specifically to prevent transaction-processing invariants (e.g., blocking arbitrary calls, preventing double execution/fee logic changes, or state mutation during a phase meant only for signature verification) from being subverted. If the Starknet OS's constrained/re-execution semantics permit this same call inside `__validate__` while Blockifier does not, this creates two possible negative outcomes:
- A state transition produced under looser semantics than the ones enforced by the canonical (Blockifier) execution engine could still be proven "correct" by the OS, meaning the OS's accepted state-transition function is broader than what honest, Blockifier-driven sequencers can ever produce. This is a form of honest-node divergence / soundness gap between the block-building engine and the block-verifying (proving) engine.
- Since account contracts' `__validate__` typically should not be able to perform arbitrary reentrant execution, an account contract exploiting this gap during the OS's constrained execution path could bypass a security invariant intended to prevent state mutation/arbitrary calls prior to fee charging and nonce commitment, undermining wallet/dApp safety assumptions that rely on `__validate__` semantics matching Blockifier's documented restrictions.

### Likelihood Explanation
Reachability requires only a normal, unprivileged account contract's `__validate__` implementation invoking the `meta_tx_v0` syscall — no special privileges, staking, or operator access are needed; any transaction sender who controls (or deploys) an account contract can trigger this path. The divergence is deterministic and code-visible (present in every current version of the reviewed files), not dependent on a race condition or timing.

### Recommendation
Add an explicit execution-mode check to the Starknet OS's `meta_tx_v0` handling — both in `crates/starknet_os/src/hint_processor/snos_syscall_executor.rs::meta_tx_v0` and in the Cairo `execute_meta_tx_v0` function in `syscall_impls.cairo` — that rejects the syscall when the current entry point is being executed as part of `__validate__` (or, more generally, mirror Blockifier's full set of "forbidden in Validate" syscalls in the OS re-execution/verification logic so both engines enforce identical invariants). Add a regression test analogous to `disable_deploy_in_validate_mode_flag_behavior` / `test_forbidden_syscall` that specifically exercises `meta_tx_v0` from within `__validate__` under the Starknet OS execution path and asserts rejection, to guarantee parity between Blockifier and the OS going forward.

### Proof of Concept
1. Deploy an account contract whose `__validate__` implementation calls `meta_tx_v0_syscall(target_address, EXECUTE_ENTRY_POINT_SELECTOR, calldata, signature)` (structurally identical to the test helper contract already in the repo, `meta_tx_test_contract.cairo`, which exposes `execute_meta_tx_v0`, but invoked from `__validate__` instead of an external entry point).
2. Submit an invoke transaction from this account. Under Blockifier (`crates/blockifier/src/execution/syscalls/syscall_base.rs:295-297`), the call fails with `InvalidSyscallInExecutionMode` because `execution_mode == ExecutionMode::Validate`.
3. Independently drive the same call sequence through the Starknet OS's syscall executor (`crates/starknet_os/src/hint_processor/snos_syscall_executor.rs::meta_tx_v0`) or the Cairo `execute_meta_tx_v0` function with `caller_execution_context` corresponding to a validate-phase entry point — no equivalent `ExecutionMode::Validate` check exists to reject the call, allowing the OS to accept/prove a state transition that Blockifier itself would never produce for that same transaction.

Note: I could not fully verify whether some additional, not-yet-located gate exists elsewhere in the OS's execution pipeline (e.g., a global execution-mode allowlist checked before entry into `execute_syscalls`) that might independently block this call during `__validate__`; the searches performed did not surface such a check in the retrieved files, but the index may not include every relevant file. If such a gate exists elsewhere in the OS pipeline, it would need to be located and confirmed to invalidate this finding.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L221-233)
```rust
    pub fn get_class_hash_at(
        &mut self,
        contract_address: ContractAddress,
    ) -> SyscallResult<ClassHash> {
        if self.context.execution_mode == ExecutionMode::Validate {
            self.reject_syscall_in_validate_mode("get_class_hash_at")?;
        }

        self.storage_access_tracker.accessed_contract_addresses.insert(contract_address);
        let class_hash = self.state.get_class_hash_at(contract_address)?;
        self.storage_access_tracker.read_class_hash_values.push(class_hash);
        Ok(class_hash)
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L286-300)
```rust
    pub fn meta_tx_v0(
        &mut self,
        contract_address: ContractAddress,
        entry_point_selector: EntryPointSelector,
        calldata: Calldata,
        signature: TransactionSignature,
        remaining_gas: &mut u64,
    ) -> SyscallResult<Vec<Felt>> {
        self.increment_syscall_linear_factor_by(&SyscallSelector::MetaTxV0, calldata.0.len());
        if self.context.execution_mode == ExecutionMode::Validate {
            self.reject_syscall_in_validate_mode("meta_tx_v0")?;
        }
        if entry_point_selector != selector_from_name(EXECUTE_ENTRY_POINT_NAME) {
            return Err(SyscallExecutionError::Revert { error_data: vec![INVALID_ARGUMENT_FELT] });
        }
```

**File:** crates/starknet_os/src/hint_processor/snos_syscall_executor.rs (L307-317)
```rust
    fn meta_tx_v0(
        request: MetaTxV0Request,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        remaining_gas: &mut u64,
    ) -> Result<MetaTxV0Response, Self::Error> {
        if request.entry_point_selector != selector_from_name(EXECUTE_ENTRY_POINT_NAME) {
            return Err(handle_failure(INVALID_ARGUMENT_FELT));
        }
        call_contract_helper(vm, syscall_handler, remaining_gas)
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L284-367)
```text
// Executes a v0 meta transaction. Specifically, calls another contract where:
// * The signature is replaced with the given signature.
// * The caller is the OS (address 0).
// * The transaction version is replaced by 0.
// * The transaction hash is replaced by the corresponding version-0 transaction hash.
// The changes apply to the called contract and the inner contracts it calls.
func execute_meta_tx_v0{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;

    let request = cast(syscall_ptr + RequestHeader.SIZE, MetaTxV0Request*);
    local calldata_start: felt* = request.calldata_start;
    local calldata_size = request.calldata_end - calldata_start;

    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=MetaTxV0Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local contract_address = request.contract_address;
    local selector = request.selector;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local old_tx_info: TxInfo* = caller_execution_info.tx_info;

    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Sanity check: Verify that `signature` is a valid Sierra array.
    assert_nn_le(request.signature_end - request.signature_start, SIERRA_ARRAY_LEN_BOUND - 1);

    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Compute the meta-transaction hash.
    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let meta_tx_hash = compute_meta_tx_v0_hash(
            contract_address=contract_address,
            entry_point_selector=selector,
            calldata=calldata_start,
            calldata_size=calldata_size,
            chain_id=old_tx_info.chain_id,
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);

    // Prepare execution context.
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        signature_start=request.signature_start,
        signature_end=request.signature_end,
        transaction_hash=meta_tx_hash,
        chain_id=old_tx_info.chain_id,
        nonce=0,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-360)
```text
    if (selector == SEND_MESSAGE_TO_L1_SELECTOR) {
        execute_send_message_to_l1(
            contract_address=execution_context.execution_info.contract_address
        );
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }

    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
    %{ OsLoggerExitSyscall %}
    return execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
```
