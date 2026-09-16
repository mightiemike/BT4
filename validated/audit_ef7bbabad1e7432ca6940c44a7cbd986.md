## Finding [1](#0-0) 

### Title
L1 handler execution effects are committed to state before the paid-fee sanity check, causing state to diverge from the transaction's returned result - (File: `crates/blockifier/src/transaction/l1_handler_transaction.rs`)

### Summary
In `L1HandlerTransaction::execute_raw`, the transactional state produced by running the L1-handler's execution (e.g., a bridge deposit that mints/credits tokens to a recipient) is **committed into the caller's state before** the function checks whether `paid_fee_on_l1` is non-zero. If the check fails, the function returns a hard `Err(...)` — yet the state mutation has already been written to the shared `state` object.

### Finding Description
`execute_raw` runs the L1 handler in a nested `TransactionalState`, then, if the resulting gas usage is within `l1_handler_bounds`, immediately calls `execution_state.commit()` on line 100 — *before* checking `self.paid_fee_on_l1`: [1](#0-0) 

If `paid_fee == Fee(0)`, the function returns `Err(TransactionExecutionError::TransactionFeeError(...))` at lines 106-113 — an error variant, not the graceful `Ok(execution_info_with_revert_error)` pattern used everywhere else in this function (compare to the `abort()` + `Ok(...)` handling used for the `fee_check_error` branch at lines 117-129, and for the execution-error branch at lines 132-141). This is the only branch in `execute_raw` where the transactional-state mutation is committed to the parent state and the function still surfaces a hard error afterward, instead of aborting.

This is the mirror image of the reported `CampTimelockEscrowNativeOFT.sol` bug: there, `bridgeOut()` recorded an amount without validating it against the fee ultimately required by `send()`, so the contract's accounting diverged from its real balance/obligations. Here, the L1-handler's state effects (e.g., minting bridged tokens on L2) are unconditionally persisted to state irrespective of whether the transaction is ultimately treated as valid/fee-paid, because the commit happens ahead of the fee validation that can still reject the transaction.

### Impact Explanation
Because `execution_state.commit()` writes directly into the `state: &mut TransactionalState<'_, U>` parameter passed into `execute_raw` (the actual state instance used by the block builder / OS re-execution for subsequent transactions and the final state diff), an `Err` return after commit does not roll back that mutation. Any code path that treats an `Err` from transaction execution as "this transaction is not part of the block" (excluding it from the transaction list, receipts, or fee bookkeeping) will nonetheless leave its state effects (e.g., a bridged deposit credited to a user, or a message marked/consumed) baked into the state that is committed and hashed into the block. This can produce:
- A state root that does not match the officially included/executed transaction list — a form of wrong committed root.
- Divergence between honest nodes/re-executors if they treat the error differently (e.g., panicking vs. silently including the mutated state), since Starknet OS re-execution and the blockifier need to agree on exactly which effects were applied.

### Likelihood Explanation
`paid_fee_on_l1` is a field of the `L1HandlerTransaction` populated from data emitted by the L1 core contract's message-sending event; the code comment itself notes it is "covered by the starknet core contract" and is only checked here "for now." While the L1 core contract is expected to enforce a non-zero fee under normal conditions, this in-sequencer check exists specifically to catch cases where it is not enforced/violated — meaning the vulnerable code path is reachable whenever an L1 message with `paid_fee_on_l1 == 0` is delivered and successfully executed within resource bounds, which is a scenario the code anticipates could occur.

### Recommendation
Reorder the logic so the `paid_fee_on_l1` check happens **before** `execution_state.commit()`, mirroring the existing `fee_check_error` branch: call `execution_state.abort()` and return an `Ok(...)` execution info carrying a `revert_error`, instead of committing the transactional state and then returning a hard `Err` for the zero-fee case.

### Proof of Concept
Not independently reproduced with a runnable test due to tool-call limits; the described flaw is demonstrated directly by the code ordering in `crates/blockifier/src/transaction/l1_handler_transaction.rs` lines 97-116, where `execution_state.commit()` (line 100) precedes the `paid_fee == Fee(0)` check/`Err` return (lines 103-113), in contrast to the sibling error branches at lines 117-141 which call `execution_state.abort()` before returning. I was unable to fully trace, within the available iterations, how the top-level block-building caller (in `transaction_execution.rs` / block builder) handles an `Err` returned from `execute_raw` for L1-handler transactions specifically (e.g., whether it panics, retries, or silently drops the transaction while retaining the already-committed state), so the exact downstream consequence (wrong state root vs. sequencer crash vs. safe rejection) could not be conclusively confirmed and should be verified against the block-building/mempool-exclusion code before treating this as fully proven.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L97-116)
```rust
                match fee_check_report {
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
                    }
```
