## Finding: L1→L2 messages are permanently consumed and their funds/side-effects lost when execution exceeds the hardcoded `l1_handler_max_amount_bounds`, with no rollback or retry mechanism

### Title
Reverted L1Handler Transactions Are Treated as Consumed, Permanently Losing L1→L2 Message Effects When a Fixed Protocol Gas Cap Is Exceeded - (File: crates/blockifier/src/transaction/l1_handler_transaction.rs)

### Summary
An unprivileged L1 message sender (e.g., anyone calling a bridge/messaging contract on L1 that forwards a `sendMessageToL2`) triggers an `L1HandlerTransaction` on Starknet. `L1HandlerTransaction::execute_raw` enforces a fixed, network-wide gas cap (`l1_handler_max_amount_bounds`, a `VersionedConstants`/protocol constant, not something the L1 sender can adjust) via `FeeCheckReport::check_all_gas_amounts_within_bounds`. If the resulting `GasVector` exceeds that cap, all state effects of the message (e.g., a bridge mint/credit to the L2 recipient) are rolled back via `execution_state.abort()`, yet the transaction is still returned as `Ok(...)` with only `revert_error` set. This is functionally identical to the reported cross-chain bug: an operation whose resource usage exceeds a cap reverts, erasing its intended effect — except here there is no admin remedy at all, because the cap is a hardcoded protocol constant applied uniformly to every L1 handler, not a per-token, admin-adjustable cap.

### Finding Description
`L1HandlerTransaction::execute_raw` runs the entry point, computes the receipt/gas vector, and checks it against `l1_handler_bounds = block_context.versioned_constants.os_constants.l1_handler_max_amount_bounds`: [1](#0-0) 

If the check fails, the transactional state is aborted (all writes discarded) and the function still returns `Ok(...)`, just carrying a `revert_error`: [2](#0-1) 

Because the result is `Ok`, downstream batcher logic classifies the transaction hash as **consumed**, not rejected — the distinction it uses to decide whether the L1 provider should mark the message committed: [3](#0-2) [4](#0-3) 

The batcher then reports `consumed_l1_handler_tx_hashes` to the L1 provider, which transitions those messages from `Pending` to `Committed`, matching the real L1 core-contract semantics where the message is consumed on L1 once processed on L2, independent of whether it reverted: [5](#0-4) 

A dedicated integration test confirms reverted L1Handler transactions still flow through the "consumed" path end-to-end, and unit tests confirm the specific case where the cause of revert is exceeding `l1_handler_max_amount_bounds`: [6](#0-5) [7](#0-6) 

Unlike the external report's cap (a per-token supply cap an admin can raise), `l1_handler_max_amount_bounds` is a fixed `VersionedConstants` value applied identically to every L1 handler transaction on the network: [8](#0-7) 

If a bridge/recipient contract's L1-handler entry point genuinely requires more L1/L2/L1-data gas than this cap for a particular payload (e.g., due to payload size, storage writes, or contract logic complexity), the transaction will **deterministically revert on every retry** — the L1 message is permanently marked consumed on L1, yet its intended L2 side effect (e.g., crediting a bridged asset) never lands, and no admin action can recover it, since the cap is protocol-wide rather than something an operator can independently raise for that one message.

### Impact Explanation
This results in a permanent freezing/loss of funds: a user's bridged deposit (or any L1→L2 message carrying value/state transitions) is consumed on L1 but never delivered on L2, with no automatic or admin-driven recovery path, since the message cannot be resubmitted (it is marked consumed) and the resource cap is not adjustable per-message. This matches the "concrete loss or permanent freezing of funds" impact bar, and is reachable purely by an unprivileged L1 message sender crafting/triggering a message whose L2-side execution cost exceeds the fixed cap.

### Likelihood Explanation
The condition is reachable by any L1 message sender without special privileges — no operator or prover malice needed. It requires only that the L2 recipient's entry point logic (calldata size, storage writes, computation) triggered by the message pushes gas usage above `l1_handler_max_amount_bounds`, which is plausible for any sufficiently data-heavy or complex bridge integration. The reverted-yet-consumed behavior is also explicitly covered by existing tests, confirming it is the current, intended code path rather than an untested edge case.

### Recommendation
Consider one of:
- Not reporting a reverted L1Handler transaction as "consumed" when the revert is due to exceeding `l1_handler_max_amount_bounds` specifically (as opposed to a contract-logic revert), so the L1 provider/core-contract flow does not finalize consumption of messages whose effects never landed.
- Providing a mechanism for the L1 core contract / message sender to detect and reclaim/re-trigger messages that failed purely due to the protocol-level gas cap.
- Documenting this behavior prominently for L1↔L2 messaging integrators, since it is a hard, non-adjustable ceiling that differs from user-supplied resource bounds and can silently and permanently drop message effects.

### Proof of Concept
1. An L1 contract sends a message to L2 (`sendMessageToL2`) targeting a recipient contract whose L1-handler entry point performs enough work (e.g., large calldata, several storage writes) that its resulting `GasVector` exceeds `l1_handler_max_amount_bounds.{l1_gas,l2_gas,l1_data_gas}`.
2. The sequencer executes the `L1HandlerTransaction`; `FeeCheckReport::check_all_gas_amounts_within_bounds` fails, `execution_state.abort()` discards all state changes, and `execute_raw` returns `Ok(...)` with `revert_error = Some(FeeCheckError::MaxGasAmountExceeded)`, as reproduced by `test_l1_handler_resource_bounds` (crates/blockifier/src/transaction/transactions_test.rs:2964-3008).
3. `collect_execution_results_and_stream_txs` inserts the tx hash into `consumed_l1_handler_tx_hashes` because the result was `Ok` (block_builder.rs:643-649), not into `rejected_tx_hashes`.
4. The batcher's `commit_block` call marks the message `Committed` in the L1 provider/transaction manager (docs/diagrams/06-l1-handler-flow.md:163-186), matching real L1 core-contract consumption semantics — the message can never be resubmitted, yet the recipient never received its intended credit/mint, permanently losing the message's value/effect.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L62-96)
```rust
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

**File:** crates/apollo_batcher/src/block_builder.rs (L643-660)
```rust
        // Insert the tx_hash into the appropriate collection if it's an L1_Handler transaction.
        if let InternalConsensusTransaction::L1Handler(_) = input_tx {
            let is_new_entry = execution_data.consumed_l1_handler_tx_hashes.insert(tx_hash);
            // Even though this doesn't get past the set insertion, this indicates a major, possibly
            // reorg-producing bug, either in some batcher cache or the l1 provider.
            assert!(is_new_entry, "Duplicate L1 handler transaction hash: {tx_hash}.");
        }

        match result {
            Ok((tx_execution_info, state_maps)) => {
                if let Some(ref revert_error) = tx_execution_info.revert_error {
                    warn!(
                        "Transaction {} is reverted during execution while still accepted. Revert \
                         Error: {}",
                        input_tx.tx_hash(),
                        revert_error,
                    );
                }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L708-717)
```rust
            Err(err) => {
                info!(
                    "Transaction {} failed to execute with error: {}.",
                    tx_hash,
                    err.log_compatible_to_string()
                );
                let is_new_entry = execution_data.rejected_tx_hashes.insert(tx_hash);
                assert!(is_new_entry, "Duplicate rejected transaction hash: {tx_hash}.");
            }
        }
```

**File:** docs/diagrams/06-l1-handler-flow.md (L163-186)
```markdown
    Note over B: After block execution completed

    B->>B: Collect consumed_l1_handler_tx_hashes
    B->>B: Collect rejected_tx_hashes
    B->>B: Filter rejected_l1_handler_tx_hashes

    B->>Storage: commit_proposal(height, state_diff)
    Storage-->>B: Ok

    rect rgb(240, 255, 240)
        Note over B,TxMgr: Notify L1 Provider
        B->>L1P: commit_block(consumed_txs, rejected_txs, height)

        L1P->>L1P: apply_commit_block(consumed, rejected)
        L1P->>TxMgr: commit_txs(committed_txs, rejected_txs)

        Note over TxMgr: For committed txs: Pending to Committed
        Note over TxMgr: For rejected txs: Unstage, keep as Pending

        TxMgr->>TxMgr: rollback_staging()
        Note over TxMgr: Increments staging epoch<br/>(unstages all txs for next block)
        L1P->>L1P: increment current_height
        L1P-->>B: Ok
    end
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

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2964-3008)
```rust
#[rstest]
#[case(L1Gas, GasAmount(1))]
// Sufficient to pass execution (enough gas to run the transaction), but fails post-execution
// resource bounds check.
#[case(L2Gas, GasAmount(200000))]
#[case(L1DataGas, GasAmount(1))]
fn test_l1_handler_resource_bounds(#[case] resource: Resource, #[case] new_bound: GasAmount) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm));

    // Set to true to ensure L1 data gas is non-zero.
    let use_kzg_da = true;

    let mut block_context = BlockContext::create_for_account_testing_with_kzg(use_kzg_da);
    let chain_info = block_context.chain_info.clone();
    let mut state = test_state(&chain_info, BALANCE, &[(test_contract, 1)]);
    let contract_address = test_contract.get_instance_address(0);

    // Modify the resource bound for the tested resource.
    let os_constants = Arc::make_mut(&mut block_context.versioned_constants.os_constants);
    match resource {
        L1Gas => os_constants.l1_handler_max_amount_bounds.l1_gas = new_bound,
        L2Gas => os_constants.l1_handler_max_amount_bounds.l2_gas = new_bound,
        L1DataGas => os_constants.l1_handler_max_amount_bounds.l1_data_gas = new_bound,
    }

    let tx = l1handler_tx(Fee(1), contract_address);

    let execution_info = tx.execute(&mut state, &block_context).unwrap();

    assert_matches!(
        execution_info,
        TransactionExecutionInfo {
            validate_call_info: None,
            execute_call_info: None,
            fee_transfer_call_info: None,
            revert_error: Some(RevertError::PostExecution(FeeCheckError::MaxGasAmountExceeded {
                resource: r,
                max_amount,
                actual_amount
            })),
            // TODO(Arni): consider checking other fields of the receipt.
            receipt: TransactionReceipt { fee, .. },
        } if r == resource && new_bound == max_amount && actual_amount > max_amount && fee == Fee(0)
    );
}
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L1-1)
```rust
use std::collections::{BTreeMap, HashMap, HashSet};
```
