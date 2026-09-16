### Title
Starknet OS `charge_fee` accepts the fee-token TRANSFER call as successful without checking its ERC20 return value - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo])

### Summary
The Starknet OS re-execution logic that charges the transaction fee (`charge_fee`) invokes the fee token contract's `transfer` entry point via `non_reverting_select_execute_entry_point_func`, but only verifies that the call did not revert — it never inspects the returned `success` boolean (`retdata`). This is the same bug class as the reported ERC20 "ignored return value" issue: an ERC20-style `transfer`/`mint` call can return `false` instead of reverting, and a caller that does not check `retdata` will incorrectly treat the operation as successful.

### Finding Description
In `charge_fee` [1](#0-0) , the OS builds a `TRANSFER_ENTRY_POINT_SELECTOR` call to the fee token contract and executes it with:
```
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```
The return values (`retdata_size`, `retdata`, `is_deprecated`) of this call are discarded entirely — the function signature returns them but `charge_fee` does not bind or check them.

Contrast this with `run_validate` in the same module [2](#0-1) , which explicitly asserts `retdata[0] = VALIDATED` after calling the same helper. The `charge_fee` path has no analogous check that the ERC20 `transfer` returned `TRUE`/success.

`non_reverting_select_execute_entry_point_func` itself only guarantees the call did not revert (`assert is_reverted = 0`) [3](#0-2)  — a compliant-looking ERC20 implementation can legally return `false` on failure (e.g., insufficient balance, paused token, blacklist, or any custom condition) without reverting, per the ERC20 spec quoted in the external report. Since `charge_fee` treats a non-reverting call as fee-collected regardless of the boolean result, this is structurally identical to the reported vulnerability class ("callers must handle false returns; callers must not assume false is never returned").

### Impact Explanation
If the fee token contract (which is a standard, potentially upgradable/custom Cairo contract at `fee_token_address`, not hardcoded bytecode enforced by the OS) returns `false` from `transfer` under some condition instead of reverting, the Starknet OS would still record the transaction fee as paid and proceed to commit the block, while the blockifier's parallel/native execution path may diverge in behavior depending on whether it inspects the retdata. This creates a risk of:
- Honest-node divergence between the OS re-execution result and the sequencer/blockifier's committed state if the two paths disagree on whether the fee transfer "succeeded", potentially causing a wrong committed root/block hash.
- Effective freezing/loss of fee funds if a token can silently no-op the transfer while the OS still finalizes the block, i.e., value is not moved but the OS output implies it was.

### Likelihood Explanation
This path is reachable by any user submitting a normal transaction, since `charge_fee` runs for every fee-paying transaction, and the fee token's behavior is not restricted by the OS beyond the assumption that transfer calls will revert on failure. The severity is bounded by the fact that the default STRK/ETH fee token implementations do revert on insufficient balance rather than returning `false`; the issue only manifests if the deployed fee token contract deviates from that convention (returns `false` without reverting). This mirrors the original report's caveat that in most cases return values are ignored safely for well-behaved tokens, but the check is still a defense-in-depth gap that should not be relied upon implicitly, especially since the fee token address is a configured value in `block_context.os_global_context.starknet_os_config.fee_token_address` rather than an OS-enforced immutable contract.

### Recommendation
In `charge_fee`, capture the `(retdata_size, retdata, is_deprecated)` returned by `non_reverting_select_execute_entry_point_func` and assert that the transfer succeeded, mirroring the pattern already used in `run_validate` (checking `retdata_size == 1` and `retdata[0]` equals the ERC20 success value) before finalizing the fee charge.

### Proof of Concept
Conceptual PoC (cannot be executed without deployment access, but root cause is concrete):
1. Configure the fee token address (`starknet_os_config.fee_token_address`) to point to a Cairo contract whose `transfer` entry point returns `felt 0` (false) instead of reverting under a certain internal condition (e.g., a custom/compromised fee token, or a legitimately upgraded token with new business logic), while still returning gas/consuming no error.
2. Submit any fee-paying transaction (invoke/declare/deploy-account) with `max_fee > 0`.
3. In `charge_fee`, the call to `non_reverting_select_execute_entry_point_func` succeeds (no revert), so execution proceeds to `return ()` at line 164 without ever checking `retdata[0]`.
4. The OS accepts the transaction as fee-paid despite the underlying `transfer` call reporting failure via its boolean return value, in contrast to the explicit check performed for `__validate__` in `run_validate`.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-165)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }

    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);

    // TODO(ilya, 01/01/2026): Consider caching the fee_token_class_hash.
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-156)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-196)
```text
func non_reverting_select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```
