This is confirmed: L1 handler transactions require `paid_fee_on_l1 > 0`, and post-execution the actual L2 gas cost (including L1 data gas cost via `to_l1_gas_for_fee`) is checked against `l1_handler_max_amount_bounds`, but the check only validates that `paid_fee > Fee(0)` — not that it covers the actual computed fee — as seen in `l1_handler_transaction.rs:103-113`.### Title
Sequencer under-compensated for L1 handler transaction execution: `paid_fee_on_l1` check only validates non-zero, not coverage of actual L2 execution + L1 DA cost - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
The reported bug class ("keeper compensated only for a fraction of the true cost of processing a transaction, because fee validation ignores real resource usage") maps directly onto how the sequencer executes `L1HandlerTransaction`s. An L1 message sender (an unprivileged, permissionless actor who can call the Starknet core contract on L1) fully controls `paid_fee_on_l1`. The sequencer executes the resulting L1 handler transaction on L2, consuming real Cairo-VM steps/Sierra gas and L1 data-availability bytes, but the only fee-sufficiency check performed is that `paid_fee_on_l1 != 0`, not that it covers the transaction's `receipt.fee` (computed from the full gas vector, including L1 data gas).

### Finding Description
`L1HandlerTransaction::execute_raw` bounds the transaction's allowed gas usage by `l1_handler_max_amount_bounds` (a generous, protocol-wide constant) and executes the message: [1](#0-0) 

After successful execution, it enforces only that consumed gas is within the max bounds: [2](#0-1) 

Then — instead of checking that `self.paid_fee_on_l1 >= receipt.fee` (the fee computed from `GasVector::cost`, which correctly folds in L1 gas, L1 data gas, and L2 gas, as seen in `starknet_api::execution_resources::GasVector::cost`) — the code only checks that a non-zero amount was paid: [3](#0-2) 

The comment on line 101-102 even acknowledges the check is a placeholder ("For now, assert only that any amount of fee was paid... covered by the starknet core contract"), and the resulting `TransactionExecutionInfo`'s `receipt.fee` is forcibly zeroed: [4](#0-3) 

This mirrors the Elf-i keeper bug precisely: the entity performing the on-chain work (there, the keeper paying L1 rollup + L2 execution fees; here, the sequencer/L2 network paying for Cairo-VM execution and L1 DA bytes to include and prove the L1 handler transaction) is not actually validated to be compensated for the true multi-resource cost of the operation — only that *some* nonzero amount was nominally paid on L1, decoupled from the resource bound (`l1_handler_max_amount_bounds`) that the transaction is permitted to consume.

### Impact Explanation
Any L1 message sender can invoke `sendMessageToL2` on the Starknet core contract with an arbitrarily small nonzero `paid_fee_on_l1` (e.g., 1 wei) while triggering an L1 handler entry point that consumes gas up to `l1_handler_max_amount_bounds` (Cairo steps/Sierra gas and state-diff/DA bytes). The sequencer/network absorbs the real L1 DA cost and L2 computation cost of processing and including these transactions in blocks, with no on-protocol mechanism forcing the sender to cover that cost beyond the constant-check placeholder. This allows an attacker to force the network to repeatedly perform maximal-cost L1 handler executions for near-zero L1 payment, externalizing/socializing the true resource cost onto the sequencer and, transitively, onto the fee market (or protocol treasury) that must otherwise absorb DA costs — a concrete, unauthorized-cost-shifting/loss vector reachable purely from an L1 message.

### Likelihood Explanation
High likelihood: triggering this requires nothing more than calling the public `sendMessageToL2` function on L1 with a minimal `value`, which any unprivileged L1 account can do. No special privileges, timing, or race conditions are required, and the check is a simple, deterministic `== Fee(0)` comparison that is trivially satisfiable with `paid_fee_on_l1 = 1`.

### Recommendation
Replace the placeholder check with a real fee-sufficiency check comparing `self.paid_fee_on_l1` against the actual computed `receipt.fee` (or at minimum against a deterministic minimal-cost estimate derived from the gas vector and current gas prices), consistent with how `AccountTransaction` fee checks validate `PostExecutionReport`/`FeeCheckReport`. If full fee coverage cannot be enforced (since the fee was fixed at L1 message-send time and gas prices may have moved), at least ensure `paid_fee_on_l1` is checked against the L1-side minimal cost commitment rather than merely being nonzero, and remove/resolve the acknowledged `TODO(Arni)` by confirming and documenting exactly which layer (L1 core contract vs. L2 blockifier) is authoritative for fee sufficiency, since currently neither layer appears to enforce actual-cost coverage on L2.

### Proof of Concept
1. An attacker calls the Starknet core contract's `sendMessageToL2` with `value = 1` wei (satisfying only "paid nonzero fee"), targeting an L1-handler entry point on L2 designed to consume near the maximum of `l1_handler_max_amount_bounds` (e.g., writing many storage slots to maximize DA gas, as in `write_a_lot`-style test contracts referenced in `transactions_test.rs`).
2. The sequencer processes the resulting `L1HandlerTransaction`; `execute_raw` runs the handler, computes `receipt.gas`/`receipt.fee` reflecting the real (large) resource cost, confirms it's within `l1_handler_max_amount_bounds`, then only checks `paid_fee_on_l1 != Fee(0)` — see [5](#0-4) .
3. Since `paid_fee_on_l1 = 1 != 0`, the transaction is accepted and committed despite the attacker having paid a negligible fraction of the true DA/execution cost, repeatable at scale for near-zero cost.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L63-72)
```rust
        let limit_steps_by_resources = false;
        let l1_handler_bounds =
            block_context.versioned_constants.os_constants.l1_handler_max_amount_bounds;

        let mut remaining_gas = l1_handler_bounds.l2_gas.0;
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context.clone(),
            limit_steps_by_resources,
            SierraGasRevertTracker::new(GasAmount(remaining_gas)),
        );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-96)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L98-115)
```rust
                    Ok(()) => {
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

                        Ok(l1_handler_tx_execution_info(execute_call_info, receipt, None))
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L146-159)
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
}
```
