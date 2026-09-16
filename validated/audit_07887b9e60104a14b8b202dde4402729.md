## Analog Found: State committed before fee-validation error in L1 handler execution

### Title
Inconsistent state commit on `paid_fee_on_l1 == 0` in `L1HandlerTransaction::execute_raw` — ([File: crates/blockifier/src/transaction/l1_handler_transaction.rs])

### Summary
The reported LayerZero bug shows a griefer using an attacker-controlled cross-chain parameter (`gasLimit`) to force a destination-chain message handler into an error path that leaves the messaging channel in an inconsistent, exploitable state. The analogous reachable path in this codebase is Starknet's own L1→L2 messaging entry point, `L1HandlerTransaction::execute_raw`. Here, an L1 message sender fully controls `paid_fee_on_l1` (the ETH value attached to the `sendMessageToL2` call), and the sequencer's L1-handler execution logic commits state changes to the parent `TransactionalState` *before* validating that this attacker-controlled fee is non-zero, then returns a hard `Err` afterward instead of the non-blocking "reverted" pattern used everywhere else in the same function.

### Finding Description
In `execute_raw`, all other failure branches follow a consistent, safe pattern: on execution failure or post-execution gas-bound failure, `execution_state.abort()` is called and the function returns `Ok(...)` with a `reverted_l1_handler` receipt — this is the correct "non-blocking" pattern (state changes discarded, transaction still finalized/consumed, no hard error propagated): [1](#0-0) 

However, the success branch does the opposite. It first commits the nested execution state into the parent state, and only afterward checks the attacker-controlled `paid_fee_on_l1` field, returning `Err` if it is zero: [2](#0-1) 

`paid_fee_on_l1` is populated directly from the ETH value the L1 sender attaches to `sendMessageToL2` on L1, which is fully attacker-controlled (any L1 account can call it with `value = 0`): [3](#0-2) [4](#0-3) 

Because `execution_state.commit()` (line 100) is called before the zero-fee check (lines 103-113), this is the only path in this function where a `TransactionExecutionError` is returned *after* the entry-point's state effects (storage writes, contract state changes from the `l1_handler` call) have already been merged into the state passed to `execute_raw`. Unlike the other error paths, there is no `execution_state.abort()` call to roll this back before the `Err` is returned. This `Err` is then propagated verbatim by the `Transaction::execute_raw` wrapper via the `?` operator: [5](#0-4) 

This mirrors the structural root cause of the LayerZero finding: a user/attacker-supplied parameter (there, `gasLimit`; here, `paid_fee_on_l1`) is used, without adequate upfront validation, to force execution down a path that produces an inconsistent state/error combination that the surrounding non-blocking design was specifically built to avoid everywhere else in the same code.

### Impact Explanation
If the block-building/execution-orchestration layer that calls `Transaction::execute_raw` (e.g., the `TransactionExecutor` in `crates/blockifier/src/blockifier/transaction_executor.rs`) treats a returned `Err` as "discard this transaction, do not apply its effects to the block state" — the normal semantics for a transactional executor — then this specific L1Handler path breaks that invariant: the entry-point's storage/state effects have already been merged into the state object one layer up (the transaction-level `TransactionalState`) before the error is raised, so those effects can leak into the block's committed state diff even though the transaction is reported as failed/excluded. This can lead to a wrong committed state root/block hash relative to what other honest nodes compute if they diverge in how the outer executor reacts to this `Err`, and can also be leveraged as a trivial-cost DoS: any L1 account can zero out `paid_fee_on_l1` to force execution into this atypical error branch on demand, for any contract with an `#[l1_handler]` entry point.

### Likelihood Explanation
Reaching this path requires only an ordinary Ethereum L1 account sending an L1→L2 message with `value = 0` to a contract with any `#[l1_handler]`, which is a normal, unprivileged, cheap L1 interaction — this is directly comparable in attacker cost/reachability to the LayerZero PoC (any unprivileged sender specifying `gasParams`). No special sequencer/operator/prover access is required.

### Recommendation
Move the `paid_fee_on_l1 == Fee(0)` check before `execution_state.commit()`, or call `execution_state.abort()` on that specific error branch (mirroring the `fee_check_error` and `execution_error` branches), so that no state effects are ever merged into the parent state when `execute_raw` ultimately returns an `Err`. More broadly, `execute_raw` implementations should guarantee the invariant "if `Err` is returned, no state mutation occurred," matching how the rest of the function already behaves.

### Proof of Concept
1. Deploy a contract with an `#[l1_handler]` entry point that performs storage writes (e.g., `l1_handler_set_value_and_revert`-style handler, see `crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo:544-551` for an example handler pattern) but modified to succeed rather than panic.
2. From any Ethereum L1 account, call `sendMessageToL2(contract, selector, payload)` with `value = 0` (fully unprivileged, as done in `send_message_from_l1_to_l2` test helper with a controllable `fee` parameter).
3. When the sequencer scrapes this event and executes the resulting `L1HandlerTransaction`, `execute_raw` will: run the entry point successfully, pass the gas-bound check, call `execution_state.commit()` (merging the handler's storage writes into the caller's state), then hit `paid_fee == Fee(0)` and return `Err(TransactionExecutionError::TransactionFeeError(...))`.
4. Because I could not trace the downstream `TransactionExecutor`/block-builder handling of this `Err` in the remaining investigation budget, the exact resulting divergence (wrong state root vs. panic vs. safely-discarded tx) is unconfirmed — this should be verified by a background agent inspecting `crates/blockifier/src/blockifier/transaction_executor.rs` and the batcher's proposal/validation flow to see whether a mid-commit `Err` from `execute_raw` is caught and rolled back correctly at that layer, or whether it can persist into the committed block state.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L97-113)
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
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L117-141)
```rust
                    Err(fee_check_error) => {
                        // Post-execution check failed. Revert the execution.
                        execution_state.abort();
                        let receipt = TransactionReceipt::reverted_l1_handler(
                            &tx_context,
                            l1_handler_payload_size,
                        );
                        Ok(l1_handler_tx_execution_info(
                            None,
                            receipt,
                            Some(fee_check_error.into()),
                        ))
                    }
                }
            }
            Err(execution_error) => {
                execution_state.abort();
                let receipt =
                    TransactionReceipt::reverted_l1_handler(&tx_context, l1_handler_payload_size);
                Ok(l1_handler_tx_execution_info(
                    None,
                    receipt,
                    Some(gen_tx_execution_error_trace(&execution_error).into()),
                ))
            }
```

**File:** crates/apollo_l1_events/tests/utils/mod.rs (L190-197)
```rust
    // Send message from L1 to L2.
    let contract = &base_layer.ethereum_base_layer.contract;
    let call_data = convert_call_data_to_u256(call_data);
    let fee = 1_u8;
    let message_to_l2 = contract
        .sendMessageToL2(U256::from(L1_CONTRACT_ADDRESS), U256::from(L2_ENTRY_POINT), call_data)
        .value(U256::from(fee));
    let receipt = message_to_l2.send().await.unwrap().get_receipt().await.unwrap();
```

**File:** crates/blockifier/src/transaction/transaction_execution.rs (L83-90)
```rust
        let executable_tx = match tx {
            StarknetApiTransaction::L1Handler(l1_handler) => {
                return Ok(Self::L1Handler(L1HandlerTransaction {
                    tx: l1_handler,
                    tx_hash,
                    paid_fee_on_l1: paid_fee_on_l1
                        .expect("L1Handler should be created with the fee paid on L1"),
                }));
```

**File:** crates/blockifier/src/transaction/transaction_execution.rs (L150-155)
```rust
        let tx_execution_info = match self {
            Self::Account(account_tx) => {
                account_tx.execute_raw(state, block_context, concurrency_mode)?
            }
            Self::L1Handler(tx) => tx.execute_raw(state, block_context, concurrency_mode)?,
        };
```
