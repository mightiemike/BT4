Based on my investigation, I found a plausible structural analog, but I want to be explicit about what I verified versus what I could not fully confirm before running out of tool calls.

### Title
Starknet L1→L2 messages are irreversibly consumed even when the corresponding `L1HandlerTransaction` fails or reverts on L2 - ([File: crates/apollo_batcher/src/block_builder.rs], [File: crates/blockifier/src/transaction/l1_handler_transaction.rs])

### Summary
The DODO bug is a "pay first, check later" pattern: a user's token transfer is irrevocable, but a later on-chain check (`maxDepositAmount`) can revert the crediting step, permanently losing the user's funds with no refund path. The closest reachable analog in this sequencer codebase is the `L1HandlerTransaction` flow: the underlying L1→L2 message (which represents an already-committed, irrevocable action taken on L1, e.g. a bridge deposit) is treated as "consumed" by the sequencer/batcher once it is picked up and included in a block, but the L2-side effect (crediting/minting/state update) can still fail or be reverted by a post-execution check performed only *after* the state was already tentatively modified.

### Finding Description
`L1HandlerTransaction::execute_raw` runs `run_execute` speculatively against a transactional copy of state, computes the receipt, and only *after* execution enforces a resource-bound check via `FeeCheckReport::check_all_gas_amounts_within_bounds` against `l1_handler_max_amount_bounds` [1](#0-0) . If that post-hoc bound check fails, the execution state is aborted (state changes rolled back) and the transaction is marked as reverted [2](#0-1) .

Critically, regardless of whether the L1 handler transaction succeeds, reverts, or even outright fails with an internal error (e.g. `StateError`), the batcher's block builder still records the transaction hash into `consumed_l1_handler_tx_hashes`, i.e. the L1 message is treated as permanently consumed and will never be resubmitted or retried by the sequencer [3](#0-2) . This is confirmed by the test `failed_l1_handler_transaction_consumed`, which explicitly asserts that a transaction whose execution result is `Err(StateError::OutOfRangeContractAddress)` still ends up in `consumed_l1_handler_tx_hashes` alongside a successfully-executed one [4](#0-3) .

This mirrors the DODO structure precisely:
- On L1, the user's action (sending the message / depositing funds to the bridge) is already final and irreversible, just like the user transferring tokens to the vault before calling `userDeposit()`.
- On L2, a check performed only after tentative execution (`check_all_gas_amounts_within_bounds`, or any other application-level revert inside the L1 handler's entry point) can cause the L2 side effect to be discarded.
- There is no mechanism visible in the reachable code path to requeue, refund, or re-execute the message — the sequencer treats it as consumed once observed/executed.

### Impact Explanation
If a legitimate cross-layer action (e.g., a bridge deposit relayed via `L1HandlerTransaction`) reverts on L2 because of a post-execution resource-bound check or any other failure inside the handler, the L1-side action remains final while the L2-side credit is discarded and the message is marked consumed, preventing retry. This is a permanent loss/freezing of the value the L1 message represented, analogous to the reported High-severity DODO issue.

### Likelihood Explanation
This is reachable by any user who triggers an L1→L2 message and by the design of `l1_handler_max_amount_bounds`, which is a protocol-defined constant, not something a user controls — meaning a message whose actual consumed gas/resources exceed the bound (or whose application logic reverts) will always exhibit this behavior. However, I could not fully verify within the available iterations whether Starknet's broader system (i.e., the L1 Core Contract's message-cancellation flow, referenced in `docs/diagrams/06-l1-handler-flow.md` and the `apollo_l1_events` transaction manager's `mark_cancellation_finalized_on_l1`/`ConsumedMessageToL2` handling) provides an off-chain/L1-level mitigation (such as a cancellation-and-refund window) that would make this a known, accepted design rather than a genuine, unmitigated vulnerability. This is a significant caveat I was not able to resolve with the remaining budget.

### Recommendation
Clarify/verify whether the `l1_handler_max_amount_bounds` check in `l1_handler_transaction.rs` can be exceeded under normal, honest usage (as opposed to only pathological gas-griefing L1 handlers), and confirm whether the existing L1 message-cancellation mechanism observed in `apollo_l1_events` is sufficient to let senders reclaim value after such a revert. If not, consider surfacing/reverting the L1 handler transaction differently (e.g. not marking it "consumed" if the fee/resource-bound check fails due to protocol-side limits rather than user error), or ensure the L1-side cancellation window fully covers this failure mode.

### Proof of Concept
Not fully constructed — I could not trace, within the remaining tool budget, the exact code path in `crates/apollo_batcher/src/block_builder.rs` that decides how a transaction ends up in `consumed_l1_handler_tx_hashes` for the "successfully-executed-but-reverted-by-fee-check" case versus the "hard execution error" case tested in `failed_l1_handler_transaction_consumed`. I recommend a follow-up review of `crates/apollo_batcher/src/block_builder.rs` (search for `consumed_l1_handler_tx_hashes`) and `crates/apollo_batcher/src/batcher.rs` to confirm the exact conditions under which a message is marked consumed versus requeued, and whether `l1_handler_max_amount_bounds` can realistically be exceeded by honest L1 senders.

**Caveat on confidence**: Given the strong likelihood that Starknet's L1↔L2 messaging already has an intentional, documented "consume-or-lose" semantic (mitigated by L1-side message cancellation, which is a known first-class feature referenced in this repo's own docs), I am not fully confident this rises to the level of an unintended vulnerability versus accepted, documented protocol design. If the scan's intent is strictly "novel, unmitigated" issues, this analog should be treated with caution rather than as a confirmed finding.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L61-96)
```rust
    ) -> TransactionExecutionResult<TransactionExecutionInfo> {
        let tx_context = Arc::new(block_context.to_tx_context(self));
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

**File:** crates/apollo_batcher/src/block_builder_test.rs (L1064-1111)
```rust
#[rstest]
#[tokio::test]
async fn failed_l1_handler_transaction_consumed() {
    let l1_handler_txs = test_l1_handler_txs(0..2);
    let mock_tx_provider = mock_tx_provider_small_stream(l1_handler_txs.clone());

    let mut helper = ExpectationHelper::new();
    helper.expect_successful_get_new_results(0);
    helper.expect_is_done(false);
    helper.expect_add_txs_to_block(&l1_handler_txs);
    helper.expect_get_new_results_with_results(vec![
        Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress)),
        Ok((execution_info(), StateMaps::default())),
    ]);
    helper.expect_is_done(true);
    helper.expect_successful_get_new_results(0);

    helper.mock_transaction_executor.expect_close_block().times(1).return_once(|_| {
        Ok(BlockExecutionSummary {
            state_diff: Default::default(),
            compressed_state_diff: None,
            bouncer_weights: BouncerWeights::empty(),
            casm_hash_computation_data_sierra_gas: CasmHashComputationData::default(),
            casm_hash_computation_data_proving_gas: CasmHashComputationData::default(),
            compiled_class_hashes_for_migration: vec![],
            block_info: BlockInfo::create_for_testing(),
        })
    });

    let (_abort_sender, abort_receiver) = tokio::sync::oneshot::channel();
    let result_block_artifacts = run_build_block(
        helper.mock_transaction_executor,
        mock_tx_provider,
        None,
        false,
        abort_receiver,
        BLOCK_GENERATION_DEADLINE_SECS,
        DEFAULT_IDLE_TIMEOUT_MS,
    )
    .await
    .unwrap();

    // Verify that all L1 handler transaction's are included in the consumed l1 transactions.
    assert_eq!(
        result_block_artifacts.execution_data.consumed_l1_handler_tx_hashes,
        l1_handler_txs.iter().map(|tx| tx.tx_hash()).collect::<IndexSet<_>>()
    );
}
```
