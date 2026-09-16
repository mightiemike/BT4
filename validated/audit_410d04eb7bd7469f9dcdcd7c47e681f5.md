### Title
L1 handler transactions with zero L1 fee are permanently un-executable, freezing the underlying L1→L2 message forever - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
The reported bug class is: a downstream, largely optional/secondary check ("child" logic) causing an otherwise-successful parent operation to fail entirely, with no graceful degradation, leading to a permanent inability to complete a legitimate user action and a freeze of the associated funds/state. The Starknet sequencer contains an analogous pattern in `L1HandlerTransaction::execute_raw`, where a post-hoc, effectively redundant fee sanity-check turns a successfully executed and already-committed L1→L2 message handler into a hard, non-retriable error, deterministically and permanently, because the checked field is immutable per-message.

### Finding Description
`L1HandlerTransaction::execute_raw` first executes the L1-handler entry point and, on success, commits the transactional sub-state (`execution_state.commit()`), then performs a resource-bound check. If that passes, the code performs an additional, explicitly-noted-as-redundant sanity check on the fee paid on L1: [1](#0-0) 

```
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
    ...
```

`paid_fee_on_l1` is a fixed field of the `L1HandlerTransaction`, populated from the fee paid to the Starknet core contract when the message was sent from L1 (see `crates/papyrus_base_layer/src/eth_events.rs:31`, `Fee(event.fee...)`). It is not something that changes across execution attempts — it is intrinsic to the specific L1→L2 message.

When this check fails, `execute_raw` returns an `Err`. The caller, `TransactionExecutor::execute`, treats any `Err` as a failed transaction and aborts the transactional state: [2](#0-1) 

The block builder then marks this transaction hash as rejected (while, per the l1-handler flow, still noting it in the "consumed" set that later gets filtered by `rejected_l1_handler_tx_hashes`), and the L1 provider keeps the transaction as "Pending" so it will be retried in a future block (see `docs/diagrams/06-l1-handler-flow.html:171-188` and `crates/apollo_batcher/src/block_builder.rs:623-721`).

Because `paid_fee_on_l1` never changes between retries, any L1→L2 message sent with `paid_fee_on_l1 == Fee(0)` will fail this exact same check on every single retry, forever. Unlike a normal Cairo revert (which is properly represented via `TransactionExecutionInfo::revert_error` and consumes/commits the transaction with a reverted receipt, as done a few lines above for `fee_check_error`), this specific failure mode bypasses the graceful-revert machinery entirely and returns a hard `TransactionExecutionError`, discarding the already-successful, already-committed execution and leaving the transaction permanently stuck in "pending/retry" limbo.

### Impact Explanation
Any L1-to-L2 message (e.g., a bridge deposit, cross-layer call, or any application-level L1 handler invocation) that is sent with zero attached fee becomes permanently unexecutable on L2. The corresponding state change (e.g., crediting L2 tokens, unlocking L2-side logic) can never occur, even though the entry point itself executes successfully and passes all resource-bound checks. This constitutes a permanent freeze of the funds/action associated with that specific L1 message, since the underlying condition (`paid_fee_on_l1`) is immutable and the transaction will be rejected identically on every future retry.

### Likelihood Explanation
This is directly reachable by any L1 message sender: sending a message to the Starknet core contract's `sendMessageToL2`-style entry point with zero value (or targeting an application flow that legitimately allows fee-less L1 handlers) produces an `L1HandlerTransaction` with `paid_fee_on_l1 == Fee(0)`. No sequencer/operator privilege is required — an ordinary L1 caller triggers this deterministically.

### Recommendation
Treat the zero-fee condition the same way the adjacent resource-bound failure is treated: instead of returning a hard `Err(TransactionExecutionError::TransactionFeeError(...))` that aborts the whole transaction unrecoverably, abort the execution state and construct a proper reverted `TransactionExecutionInfo` (as done for `fee_check_error` a few lines below), or reject the transaction once and definitively remove it from the retry queue rather than looping forever. At minimum, ensure this check cannot silently and permanently strand a message whose execution otherwise succeeds.

### Proof of Concept
1. An L1 contract sends a message to L2 targeting a valid contract/`l1_handler` entry point, with `msg.value` (fee) equal to `0`.
2. The sequencer's L1 events scraper picks up the `LogMessageToL2` event and constructs an `L1HandlerTransaction` with `paid_fee_on_l1 = Fee(0)`.
3. During block building, `L1HandlerTransaction::execute_raw` executes the handler successfully, commits the sub-state, and passes the resource-bound check (`crates/blockifier/src/transaction/l1_handler_transaction.rs:97-100`).
4. The `paid_fee == Fee(0)` check fails, returning `Err(TransactionExecutionError::TransactionFeeError(...))` (lines 106-113).
5. `TransactionExecutor::execute` aborts the transactional state and returns `TransactionExecutorError::TransactionExecutionError`, causing the block builder to reject the transaction (`crates/apollo_batcher/src/block_builder.rs:708-716`).
6. The L1 provider keeps the transaction pending for retry in subsequent blocks; since `paid_fee_on_l1` never changes, step 3-5 repeats identically forever, and the message's intended L2 effect never occurs.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L98-113)
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
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L176-181)
```rust
            }
            Err(error) => {
                transactional_state.abort();
                Err(TransactionExecutorError::TransactionExecutionError(error))
            }
        }
```
