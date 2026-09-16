Based on my investigation, I found a legitimate analog in the sequencer's L1 handler fee-charging logic that mirrors the Y2K bug class — a transaction path that requires only a token non-zero fee payment instead of enforcing the actual computed fee.

### Title
L1Handler transactions bypass proper fee enforcement, accepting any non-zero `paid_fee_on_l1` regardless of actual computed fee - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
`L1HandlerTransaction::execute_raw` computes the transaction's `actual_fee` (`receipt.fee`) from the true gas/resources consumed, but the only enforcement check performed against the fee the L1 sender paid on L1 (`self.paid_fee_on_l1`) is that it is non-zero — the actual amount is never compared to the computed `receipt.fee`.

### Finding Description
In `execute_raw`, after resource-bound checks pass and the state is committed, the code does: [1](#0-0) 
This block reads `paid_fee = self.paid_fee_on_l1` and rejects the transaction only `if paid_fee == Fee(0)`. Any non-zero value — including `Fee(1)` — passes, regardless of how it compares to `receipt.fee`, which is the actually computed required fee based on real gas consumption. The comment in the code itself acknowledges this: "For now, assert only that any amount of fee was paid. The error message still indicates the required fee," and a further TODO states the check may even be removable since it is "covered by the starknet core contract" [2](#0-1) . Unlike account transactions, whose actual fee is transferred on L2 via `handle_fee`/`execute_fee_transfer` [3](#0-2) , L1 handler transactions have `fee_transfer_call_info: None` and their `receipt.fee` is forced to `Fee(0)` in the returned execution info [4](#0-3) , i.e., no on-L2 fee collection ever happens for L1 handlers — the only price signal is the (unchecked) `paid_fee_on_l1` value baked into the L1→L2 message.

This is structurally analogous to the Y2K "treasury tax bypass" bug: in that report, the vault charges a proper percentage-based `depositFee` on the main "taxed" path (`deposit`), but an alternate path (`mintDepositInQueue`) only enforces a small fixed `relayerFee`, letting anyone route deposits through the cheap path and skip the real fee. Here, the "real" per-resource fee (`receipt.fee`, computed from actual L2 gas usage — same as an ordinary transaction's fee) is never actually collected or reconciled against what the L1 sender paid; the check degenerates to "was some non-zero fee attached on L1," which is entirely under the control of whoever sends the L1→L2 message (the L1 sender chooses `paid_fee_on_l1` when calling the StarknetCore `sendMessageToL2`).

### Impact Explanation
Because `paid_fee_on_l1` is attacker-controlled from L1 and the sequencer/OS never verifies it covers `receipt.fee`, L1 handler transactions can consume L2 execution resources (up to `l1_handler_max_amount_bounds`) while paying an arbitrarily small, non-zero fee. This is a resource/fee-accounting bypass rather than a token-fee bypass, but it means the sequencer performs unbounded-relative-to-payment work for L1 handlers, undermining the fee model's guarantee that resource consumption is compensated proportionally — the same class of "fee designed to be proportional but bypassable via an alternate cheap path" as the source report.

### Likelihood Explanation
Any L1 contract (or attacker-controlled L1 contract) that sends a message to an L2 contract can set `paid_fee_on_l1` to the minimum non-zero value while triggering L2 execution up to `L1_HANDLER_L2_GAS_MAX_AMOUNT`/`l1_handler_max_amount_bounds`. No special privilege is needed beyond being able to call `sendMessageToL2` on L1, which is generally permissionless for any L1 contract wired to invoke an L1 handler.

### Recommendation
Compare `paid_fee_on_l1` against the actual computed `receipt.fee` (or a fee derived consistently with account-transaction fee accounting) rather than only checking non-zero, and reject/revert L1 handler transactions whose L1-paid fee under-covers the real resource cost, consistent with how `FeeCheckReport::check_all_gas_amounts_within_bounds` already enforces resource *amount* bounds at [5](#0-4) .

### Proof of Concept
1. Deploy an L1 contract that calls the Starknet core contract's `sendMessageToL2` targeting an L2 contract's `#[l1_handler]` entrypoint that performs non-trivial (but within `l1_handler_max_amount_bounds`) computation/storage writes.
2. Set the L1 message fee (`paid_fee_on_l1`) to the smallest possible non-zero value (e.g., 1 wei).
3. The sequencer executes the L1 handler, computes `receipt.fee` based on actual resource use (which may be far higher), but the check at [6](#0-5)  only verifies `paid_fee != Fee(0)`, so the transaction is accepted and committed despite the nominal fee being far below the actual computed cost.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-96)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L99-113)
```rust
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L146-158)
```rust
fn l1_handler_tx_execution_info(
    execute_call_info: Option<CallInfo>,
    mut receipt: TransactionReceipt,
    revert_error: Option<RevertError>,
) -> TransactionExecutionInfo {
    receipt.fee = Fee(0);
    TransactionExecutionInfo {
        validate_call_info: None,
        execute_call_info,
        fee_transfer_call_info: None,
        receipt,
        revert_error,
    }
```

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
