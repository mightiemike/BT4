Based on the investigation, I found a genuine analog in the L1 handler transaction execution path.

### Title
L1Handler post-execution resource-bound revert still consumes the L1→L2 message, causing permanent, unrecoverable loss of bridged funds/state - ([File: crates/blockifier/src/transaction/l1_handler_transaction.rs])

### Summary
The external report describes a class of bug where a strict, all-or-nothing check (asked-amount vs. actual balance) causes a transaction to revert entirely, while the "grieving" side-effect (front-run partial consumption) is irreversible, leading to protocol loss. The Starknet sequencer analog is in `L1HandlerTransaction::execute_raw`: the L1→L2 message is consumed (nonce burned, no re-processing possible) via `consume_l1_to_l2_message` in the Starknet OS execution path *before* the post-execution fee/resource-bound check is evaluated, and if that check fails the execution state is aborted (rolled back) but the message consumption is not reversible on L1. Any unprivileged party can influence the resources actually consumed by an `l1_handler` invocation (e.g., by writing to shared storage slots the handler touches, changing calldata-dependent execution paths, or otherwise increasing gas usage right before the handler runs), pushing actual resource usage above the fixed `l1_handler_max_amount_bounds`.

### Finding Description
`L1HandlerTransaction::execute_raw` executes the handler entry point and then calls `FeeCheckReport::check_all_gas_amounts_within_bounds` against the fixed `l1_handler_bounds` (`os_constants.l1_handler_max_amount_bounds`) [1](#0-0) . If the check fails, the transactional execution state is aborted (`execution_state.abort()`) and a reverted receipt with zero fee is produced [2](#0-1) . Crucially, in the Starknet OS re-execution flow, `consume_l1_to_l2_message` is invoked unconditionally prior to executing the handler's entry point, marking the L1→L2 message nonce as consumed regardless of whether the subsequent execution later reverts due to resource-bound overage [3](#0-2) . This mirrors the Arcadia bug class exactly: a strict "all resources must be within bound" check (`check_all_gas_amounts_within_bounds`, identical in structure to the Dutch-auction bid's strict amount check) that, upon failing by even a small margin, reverts the beneficial state transition — but here the analog to "asset already withdrawn" is the message-consumption side effect, which is not rolled back on L1 and cannot be retried, since the L1 core contract will not allow the same message to be replayed once its L2 nonce is marked consumed.

Since resource usage of a contract's `l1_handler` entry point commonly depends on mutable contract storage (e.g. dynamic-length arrays, existing balances, or other state the handler reads/writes), an unprivileged attacker can submit ordinary transactions immediately before the `l1_handler` transaction is sequenced (transactions are gas-price-ordered/mempool-ordered, not guaranteed FIFO per contract) to inflate the actual gas/steps consumed by the handler above the fixed `l1_handler_max_amount_bounds`, deterministically forcing the resource check to fail post-execution.

### Impact Explanation
When the resource-bound check fails, the handler's intended state transition (e.g., crediting a bridged deposit) is rolled back, yet the L1-to-L2 message is already consumed and cannot be resubmitted through the same L1 message flow. This results in permanent freezing/loss of the user's bridged funds — a direct "Bad debt"/loss-of-funds analog to the reported issue, entirely reachable by an unprivileged party issuing ordinary L2 transactions ahead of the L1 handler's sequencing.

### Likelihood Explanation
Likelihood is Medium: it requires a handler whose gas cost is state-dependent and that state to be attacker-influenceable, plus the ability to have a transaction sequenced right before the targeted `l1_handler` transaction. Given L1 handler resource bounds (`l1_handler_max_amount_bounds`) are protocol-wide fixed constants rather than per-message user-specified bounds, contracts with variable-cost `l1_handler` entry points (common in bridge/message-relay contracts) are systematically exposed.

### Recommendation
Defer `consume_l1_to_l2_message` (or make its finality contingent) until after the resource-bound/fee check succeeds, or make the resource-bound check tolerant of minor overage (e.g., charge additional fee/log an over-bound event) rather than reverting and discarding a successfully-consumed message. Alternatively, ensure the OS's `EndTx`/message-consumption logic can distinguish and safely re-queue L1 messages associated with reverted `l1_handler` executions that failed only due to resource-bound checks (as opposed to genuine business-logic reverts), or track/report over-bound resource cases separately so operators can safely resubmit compensation without losing the original L1 deposit.

### Proof of Concept
1. Attacker identifies a bridge/message-processing contract `C` whose `l1_handler` entry point's Cairo-steps/gas usage scales with existing contract storage/array length (common pattern for L1↔L2 messaging contracts that iterate over stored state).
2. Attacker submits and gets included (via normal fee-priority mempool ordering) transactions that grow `C`'s relevant storage to just below the threshold where the handler's own resource cost, when added to the growth, exceeds `l1_handler_max_amount_bounds`.
3. A legitimate user's L1→L2 deposit message targeting `C`'s `l1_handler` is finalized on L1 and enters the L2 sequencing pipeline.
4. During execution, `consume_l1_to_l2_message` is called in the Starknet OS transaction flow, consuming the message nonce [4](#0-3) ; execution then proceeds and consumes resources exceeding `l1_handler_bounds`.
5. `check_all_gas_amounts_within_bounds` fails in `L1HandlerTransaction::execute_raw`, the execution is aborted, and the transaction is recorded as reverted with the state change (funds credit) discarded [2](#0-1) .
6. The user's L1 deposit is permanently lost: the L1 message is consumed (cannot be resent as the same message), and the L2 side never credited the funds.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L63-96)
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
        let l1_handler_payload_size = self.payload_size();

        // Create a copy of the state for the execution. It will be rolled back if the execution is
        // reverted or committed upon success.
        let mut execution_state = TransactionalState::create_transactional(state);
        let execution_result =
            self.run_execute(&mut execution_state, &mut context, &mut remaining_gas);
        match execution_result {
            Ok(execute_call_info) => {
                let receipt = TransactionReceipt::from_l1_handler(
                    &tx_context,
                    l1_handler_payload_size,
                    CallInfo::summarize_many(
                        execute_call_info.iter(),
                        &block_context.versioned_constants,
                    ),
                    &execution_state.to_state_diff()?,
                );

                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L117-129)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-452)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );

    %{ EndTx %}
    return ();
}
```
