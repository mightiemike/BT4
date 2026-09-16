Confirmed root cause: in the OS `charge_fee` (Cairo) path, `non_reverting_select_execute_entry_point_func` asserts only `is_reverted = 0` — it never inspects `retdata`/`success` returned by the fee token's `transfer` entry point [1](#0-0) , whereas the analogous `run_validate` explicitly checks `retdata[0] = VALIDATED` [2](#0-1) . `charge_fee` builds the fee-transfer call and invokes this unchecked helper [3](#0-2) . On the Rust/blockifier side, `execute_fee_transfer` similarly only propagates an `Err` if the call execution itself errors/reverts, and never validates the `Felt::TRUE` return value from the ERC20 `transfer` entry point [4](#0-3) .

### Title
Unchecked fee-token `transfer` return value in `charge_fee` allows fee payment to be accepted without actual balance transfer - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo)

### Summary
The Starknet OS `charge_fee` routine (used to deduct the transaction fee from the sender and credit the sequencer) executes the fee token's `transfer` entry point through `non_reverting_select_execute_entry_point_func`, which only checks that the call did not revert (`is_reverted = 0`). It does not check the returned `success` felt (the `(success: felt)` return value defined by the ERC20 ABI, e.g. in `erc20_fee_contract_class.json`). A conforming ERC20 implementation that returns `FALSE` instead of reverting on failure (e.g., insufficient balance edge cases, paused/blacklisted transfers, or any custom fee-token logic returning `0`) would cause the OS to treat the fee as successfully charged even though no state change occurred, or an inconsistent state change occurred.

### Finding Description
`charge_fee` in `transaction_impls.cairo` constructs a `TransferCallData` and dispatches it via `non_reverting_select_execute_entry_point_func`, discarding the `success` return value entirely — it only receives `retdata_size`, `retdata`, `is_deprecated` and never asserts `retdata[0] == TRUE` [5](#0-4) . Contrast this with `run_validate`, which explicitly asserts the returned value equals `VALIDATED` for non-deprecated contracts [6](#0-5) , showing the codebase's own pattern of validating return codes is intentionally skipped for the fee transfer. The Cairo0 fee-token reference implementation itself always returns `TRUE` unconditionally after `ERC20_transfer` (which internally asserts sufficient balance via `assert_not_zero(enough_balance)`), so under the reference token this path can't diverge [7](#0-6) . However, `charge_fee` reads the fee token address from block context (`fee_token_address`) and looks up whatever class hash is currently associated with that address in state [8](#0-7) , meaning the OS logic itself makes no code-level guarantee that the deployed fee-token class always reverts on failure — the safety of the unchecked call depends entirely on an out-of-protocol assumption about the specific fee-token contract deployed at that address, not on an in-circuit invariant.

### Impact Explanation
If the deployed fee-token contract at `fee_token_address` ever returns `FALSE` (or any non-`TRUE` felt) on failure instead of reverting — which is exactly the bug class flagged in the referenced audit report for ERC-20/721 style contracts — the sequencer's OS would mark the transaction as having paid its fee while the sender's balance was never actually debited (or the transfer only partially succeeded per a nonstandard implementation), permanently freezing/misallocating fee accounting and diverging the committed state from what an honest re-execution of a "correctly returning" token would produce. Because block hash/state commitments are derived from state changes accepted at this checked-but-not-value-checked call, this could contribute to wrong committed state relative to what should have been rejected.

### Likelihood Explanation
Likelihood is constrained: the currently shipped/reference fee-token implementation always reverts on insufficient balance rather than returning `FALSE` [9](#0-8) , so under the canonical, currently-deployed STRK/ETH fee tokens this cannot be triggered by a normal transaction sender. The path is only reachable if the network's fee-token class ever changes to (or is deployed with) semantics that return a falsy success value on failure instead of reverting; the OS provides no defense-in-depth check against that scenario, unlike the parallel `__validate__` return-code check.

### Recommendation
Add an explicit assertion on the `transfer` return value in `charge_fee`, mirroring `run_validate`'s pattern: after calling `non_reverting_select_execute_entry_point_func`, assert `retdata_size = 1` and `retdata[0] = TRUE` (or the appropriate success constant) for non-deprecated contracts, so the fee-charging logic does not depend on an unstated assumption that the fee token always reverts on failure. Apply the analogous check on the Rust/blockifier side in `execute_fee_transfer`/`handle_fee` by validating the `Felt::TRUE` retdata from the fee-transfer `CallInfo` before treating the fee as collected.

### Proof of Concept
1. Assume (or in a test/local devnet, deploy) a fee-token class at `fee_token_address` whose `transfer`/`transferFrom` implementation returns `FALSE` instead of reverting when the sender's balance is insufficient (a spec-compliant but non-reverting ERC20 variant, as called out in the referenced audit finding).
2. Submit an ordinary V3 invoke transaction from an account whose fee-token balance is less than `actual_fee`.
3. During `charge_fee`, the OS calls `non_reverting_select_execute_entry_point_func`; the token call returns normally with `retdata = [FALSE]` and `is_reverted = 0`, so the `assert is_reverted = 0` passes and the return path in `charge_fee` discards `retdata` entirely [10](#0-9) [11](#0-10) .
4. The transaction is committed as fee-paid even though the sender's token balance was never decremented, producing a state/commitment divergent from a token implementation that reverts on failure.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L179-197)
```text
// Same as `select_execute_entry_point_func`, but does not support reverts and does
// not have an implicit 'revert_log' argument.
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L550-591)
```rust
    fn execute_fee_transfer(
        state: &mut dyn State,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
    ) -> TransactionExecutionResult<CallInfo> {
        // The least significant 128 bits of the amount transferred.
        let lsb_amount = Felt::from(actual_fee.0);
        // The most significant 128 bits of the amount transferred.
        let msb_amount = Felt::ZERO;

        let TransactionContext { block_context, tx_info } = tx_context.as_ref();
        let storage_address = tx_context.fee_token_address();
        // The fee contains the cost of running this transfer, and the token contract is
        // well known to the sequencer, so there is no need to limit its run.
        let mut remaining_gas_for_fee_transfer =
            block_context.versioned_constants.os_constants.gas_costs.base.default_initial_gas_cost;
        let fee_transfer_call = CallEntryPoint {
            class_hash: None,
            code_address: None,
            entry_point_type: EntryPointType::External,
            entry_point_selector: selector_from_name(constants::TRANSFER_ENTRY_POINT_NAME),
            calldata: calldata![
                *block_context.block_info.sequencer_address.0.key(), // Recipient.
                lsb_amount,
                msb_amount
            ],
            storage_address,
            caller_address: tx_info.sender_address(),
            call_type: CallType::Call,

            initial_gas: remaining_gas_for_fee_transfer,
        };
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context,
            true,
            SierraGasRevertTracker::new(GasAmount(remaining_gas_for_fee_transfer)),
        );

        Ok(fee_transfer_call
            .execute(state, &mut context, &mut remaining_gas_for_fee_transfer)
            .map_err(|error| Box::new(TransactionFeeError::ExecuteFeeTransferError(error)))?)
    }
```

**File:** crates/blockifier_test_utils/resources/ERC20/ERC20_Cairo0/ERC20_without_some_syscalls/ERC20/ERC20.cairo (L77-85)
```text
@external
func transfer{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    recipient: felt, amount: Uint256
) -> (success: felt) {
    let (sender) = get_caller_address();
    ERC20_transfer(sender, recipient, amount);

    return (TRUE,);
}
```

**File:** crates/blockifier_test_utils/resources/ERC20/ERC20_Cairo0/ERC20_without_some_syscalls/ERC20/ERC20_base.cairo (L134-159)
```text
func ERC20_transfer{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    sender: felt, recipient: felt, amount: Uint256
) {
    alloc_locals;
    assert_not_zero(sender);
    assert_not_zero(recipient);
    uint256_check(amount);  // Almost surely not needed, might remove after confirmation.

    let (local sender_balance: Uint256) = ERC20_balances.read(account=sender);

    // Validates amount <= sender_balance and returns 1 if true.
    let (enough_balance) = uint256_le(amount, sender_balance);
    assert_not_zero(enough_balance);

    // Subtract from sender.
    let (new_sender_balance: Uint256) = uint256_sub(sender_balance, amount);
    ERC20_balances.write(sender, new_sender_balance);

    // Add to recipient's balance.
    let (recipient_balance: Uint256) = ERC20_balances.read(account=recipient);
    // Overflow is not possible because sum is guaranteed by mint to be less than total supply.
    let (new_recipient_balance, _: Uint256) = uint256_add(recipient_balance, amount);
    ERC20_balances.write(recipient, new_recipient_balance);
    Transfer.emit(sender, recipient, amount);
    return ();
}
```
