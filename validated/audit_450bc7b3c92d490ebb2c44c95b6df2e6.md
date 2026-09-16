### Title
Fee-transfer execution does not validate the ERC20 `transfer` return value - ([File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
`AccountTransaction::execute_fee_transfer` builds a `CallEntryPoint` that invokes the fee token's `transfer` entry point and only checks whether the Cairo execution *reverted*; it never inspects the call's return data (the `success: felt` boolean that the ERC20 `transfer`/`transferFrom` ABI returns), mirroring the "ERC20 missing return value check" bug class from the referenced report.

### Finding Description
`execute_fee_transfer` constructs a `CallEntryPoint` targeting `constants::TRANSFER_ENTRY_POINT_NAME` on the fee token contract and simply propagates the `CallInfo` returned by `.execute(...)`, mapping only execution errors to `TransactionFeeError::ExecuteFeeTransferError`: [1](#0-0) 

The standard fee-token ERC20 implementation used/tested in this repo returns a `(success: felt)` value from `transfer`/`transferFrom`/`approve` rather than reverting on failure: [2](#0-1) 

`handle_fee` calls `execute_fee_transfer`/`concurrency_execute_fee_transfer`, wraps the resulting `CallInfo` in `Some(fee_transfer_call_info)`, and treats the transaction fee as successfully collected as long as no error was raised — it never reads `call_info.execution.retdata` to confirm the token actually reported `success = TRUE`: [3](#0-2) 

Because any fee-token class deployed on Starknet (not only the canonical STRK/ETH fee token) can theoretically implement `transfer` in a way that returns `FALSE` on failure instead of panicking/reverting (e.g., a non-compliant or intentionally malicious token used as fee token in a custom deployment, or a future/alternate fee-token class), the sequencer would record the fee transfer `CallInfo` as if funds moved, without verifying the boolean success flag returned by the contract.

### Impact Explanation
If the fee token's `transfer` call returns without reverting but reports `success = FALSE` in its return data, `handle_fee` still records a non-`None` `fee_transfer_call_info` and the transaction proceeds as if the fee was paid. This can result in the sequencer/state committing a block where fee funds were never actually debited from the sender/credited to the sequencer, i.e., unauthorized "free" transaction execution and an incorrect state root relative to what strict ERC20 semantics would imply, since honest nodes relying on strict return-value checking (if any downstream re-execution, e.g. the Starknet OS, does check it) could diverge from the blockifier's lenient behavior.

### Likelihood Explanation
This requires the deployed fee token contract to be one that returns `false` instead of reverting on transfer failure — the canonical Starknet fee token (as embedded/tested in this repo) always either succeeds or asserts/reverts, so under default mainnet conditions this path is not triggerable by an ordinary transaction. The issue would only be reachable if a permissively-implemented ERC20-like contract is configured as the fee token (e.g., in an appchain/custom deployment) — an unprivileged transaction sender can then submit a transaction that drains their approved allowance/balance state such that the fee token's internal logic elects to return `false` rather than assert, at essentially no cost, since the token's implementation choice — not sequencer configuration — determines revert-vs-return-false behavior.

### Recommendation
After executing the fee-transfer `CallEntryPoint` in `execute_fee_transfer`, decode `retdata` from the resulting `CallInfo` and explicitly verify it encodes `success = TRUE` (matching the ERC20 `transfer` ABI), returning a `TransactionFeeError` (e.g., a new `FeeTransferReturnedFalse` variant) if the check fails, instead of only relying on `.execute(...)` not returning an `Err`.

### Proof of Concept
1. Deploy an ERC20 fee-token class whose `transfer` function returns `(success: FALSE)` under some caller-triggerable internal condition instead of asserting/reverting (this is valid Cairo/Starknet contract behavior; the interface only requires the `Result`/return value, not a revert).
2. Configure this contract as the chain's fee token address.
3. Submit an ordinary invoke transaction from an account whose state causes the token's `transfer` logic to hit that `success=FALSE` branch during `execute_fee_transfer` at `crates/blockifier/src/transaction/account_transaction.rs:566-591`.
4. Observe that `handle_fee` at `crates/blockifier/src/transaction/account_transaction.rs:526-548` still returns `Ok(Some(fee_transfer_call_info))` and the transaction is treated as fee-paid, even though the fee token itself reported the transfer as unsuccessful.

### Citations

**File:** crates/blockifier/src/transaction/account_transaction.rs (L526-548)
```rust
    fn handle_fee<S: StateReader>(
        state: &mut TransactionalState<'_, S>,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
        charge_fee: bool,
        concurrency_mode: bool,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        if !charge_fee || actual_fee == Fee(0) {
            // Fee charging is not enforced in some tests.
            // TODO(Yoni): consider setting the actual fee to zero when the flag is off.
            return Ok(None);
        }

        Self::assert_actual_fee_in_bounds(&tx_context, actual_fee);

        let fee_transfer_call_info = if concurrency_mode && !tx_context.is_sequencer_the_sender() {
            Self::concurrency_execute_fee_transfer(state, tx_context, actual_fee)?
        } else {
            Self::execute_fee_transfer(state, tx_context, actual_fee)?
        };

        Ok(Some(fee_transfer_call_info))
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L566-591)
```rust
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

**File:** crates/blockifier_test_utils/resources/ERC20/ERC20_Cairo0/ERC20_without_some_syscalls/ERC20/ERC20.cairo (L77-106)
```text
@external
func transfer{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    recipient: felt, amount: Uint256
) -> (success: felt) {
    let (sender) = get_caller_address();
    ERC20_transfer(sender, recipient, amount);

    return (TRUE,);
}

@external
func transferFrom{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    sender: felt, recipient: felt, amount: Uint256
) -> (success: felt) {
    alloc_locals;
    let (local caller) = get_caller_address();
    let (local caller_allowance: Uint256) = ERC20_allowances.read(owner=sender, spender=caller);

    // Validates amount <= caller_allowance and returns TRUE if true.
    let (enough_allowance) = uint256_le(amount, caller_allowance);
    assert_not_zero(enough_allowance);

    ERC20_transfer(sender, recipient, amount);

    // Subtract allowance.
    let (new_allowance: Uint256) = uint256_sub(caller_allowance, amount);
    ERC20_allowances.write(sender, caller, new_allowance);

    return (TRUE,);
}
```
