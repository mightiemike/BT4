### Title
Unchecked return value of ERC20 `transfer` in Starknet OS `charge_fee` allows fee-collection bypass - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo)

### Summary
The Starknet OS `charge_fee` function executes the fee-token's `transfer` entry point to move the actual fee from the sender to the sequencer, but it never inspects the entry point's return data to confirm the transfer actually succeeded (i.e., that the ERC20 contract returned `TRUE`). It only asserts that the call did not *revert*, which is a materially weaker guarantee than checking the boolean success return value.

### Finding Description
In `charge_fee`, the fee transfer is dispatched via `non_reverting_select_execute_entry_point_func`, and its return tuple (`retdata_size`, `retdata`, `is_deprecated`) is discarded entirely: [1](#0-0) 

Contrast this with `run_validate`, in the same module, which calls the identical helper but explicitly captures and validates the return data before proceeding: [2](#0-1) 

`non_reverting_select_execute_entry_point_func` itself only guarantees `is_reverted == 0`; it makes no statement about the semantic return value of the invoked entry point: [3](#0-2) 

Because `charge_fee` builds a `TransferCallData` and dispatches straight to the fee token's `TRANSFER_ENTRY_POINT_SELECTOR` without capturing/asserting on the return value: [4](#0-3) 

any fee-token implementation (or any account/paymaster-controlled proxy in front of it) that returns `FALSE` instead of reverting on failure will cause the OS to treat the fee as successfully charged even though no value moved from the sender to the sequencer. This mirrors exactly the audited Solidity bug class ("unchecked return value of external transfer call") - the sequencer trusts a boolean-returning transfer without checking the boolean.

This is reachable purely by submitting a normal transaction (invoke/declare/deploy_account) from an unprivileged account whose associated ERC20 fee-token behavior can be influenced (e.g., an account/proxy that wraps a non-standard or malicious token that returns false silently, or a future fee-token version/implementation change that follows the "return false" pattern instead of reverting). No special sequencer/operator/prover privilege is required — this is standard per-transaction fee-charging logic executed for every transaction and is also independently re-executed by the Starknet OS (in scope per the review rules).

### Impact Explanation
If the fee token transfer silently fails (returns `false`) rather than reverting, `charge_fee` still returns normally, the transaction is treated as successfully paying its fee, and the sequencer's balance is never actually incremented on-chain by that transfer. This is a direct path to loss of protocol fee revenue / incorrect accounting: the account is not charged, yet the transaction's execution results and committed state assume the fee was paid. Since `charge_fee` runs unconditionally for every non-zero-fee transaction and its output silently discards the transfer's true/false result, this could also cause a divergence between the Rust blockifier path (which separately executes `TRANSFER_ENTRY_POINT_NAME` via `execute_fee_transfer` in `account_transaction.rs`) and the Starknet OS re-execution path if their success-checking semantics differ, leading to state root / block hash mismatches (honest-node divergence) if the two layers disagree on whether the fee transfer succeeded.

### Likelihood Explanation
The likelihood depends on the fee-token contract behavior always reverting on failed transfers. Standard OpenZeppelin-style ERC20 do revert, but the boolean-return no-revert pattern is a documented and common class of ERC20 non-compliance (as illustrated by the referenced report), and the fee token address is read from `block_context` state rather than hardcoded, so any migration, mock, or alternate token/proxy that follows this weaker return-value convention would trigger the issue with a plain unprivileged transaction — no attacker-controlled contract logic bypass is even required beyond controlling/using such a token.

### Recommendation
In `charge_fee`, capture the return values of `non_reverting_select_execute_entry_point_func` and assert that the transfer reports success (`retdata_size == 1` and `retdata[0] == FELT_TRUE`), mirroring the pattern already used in `run_validate` for the `VALIDATED` return value. This closes the parity gap between "did not revert" and "actually succeeded."

### Proof of Concept
1. Configure `block_context.os_global_context.starknet_os_config.fee_token_address` (or otherwise cause) the fee token's `transfer` entry point to execute without reverting but return `FELT_FALSE` (0) on insufficient balance/failure instead of reverting — a legal, non-reverting Cairo implementation.
2. Submit any ordinary invoke/declare/deploy_account transaction with `max_fee > 0` from an account whose actual token balance is insufficient to cover the fee (or whose logic intentionally returns false).
3. Observe that `charge_fee` completes without error (line 161-164, transaction_impls.cairo) because only `is_reverted == 0` is enforced by `non_reverting_select_execute_entry_point_func`; the discarded `retdata` is never checked.
4. The transaction is finalized as if the fee was collected, while the sequencer's actual fee-token balance was never incremented by this transfer, producing a fee-accounting fault that a strict return-value check would have caught.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L104-164)
```text
// Charges a fee from the user.
// If max_fee is not 0, validates that the selector matches the entry point of an account contract
// and executes an ERC20 transfer on the behalf of that account contract.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the transaction that pays the fee.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-197)
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
}
```
