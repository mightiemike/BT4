### Title
Failed (non-reverted, hard-error) L1 handler transactions are still marked "consumed", permanently discarding the L1→L2 message - (File: crates/apollo_batcher/src/block_builder.rs)

### Summary
In `collect_execution_results_and_stream_txs`, every `InternalConsensusTransaction::L1Handler` has its hash unconditionally inserted into `execution_data.consumed_l1_handler_tx_hashes` before the executor's result is even inspected. This happens regardless of whether execution succeeded, reverted, or hard-failed with an `Err` (e.g. a `StateError`). The tx hash is thus always reported to the L1 provider as "consumed" on commit, even when the transaction's real-world effect (e.g., crediting a user via an L1-to-L2 bridge deposit) never took place.

### Finding Description
`collect_execution_results_and_stream_txs` iterates the executed chunk and, for every `L1Handler` input transaction, immediately does: [1](#0-0) 
This insertion happens *before* the `match result` block that distinguishes `Ok` (successful or reverted-but-executed) from `Err` (hard execution/state error causing the transaction to be entirely excluded from the block and pushed into `rejected_tx_hashes`): [2](#0-1) 

The `apollo_l1_events` transaction manager treats "consumed" as a terminal, permanent state — once `mark_consumed` is called for a tx hash, the record can no longer be retried and is eventually purged after the timelock: [3](#0-2) [4](#0-3) 

The dedicated regression test `failed_l1_handler_transaction_consumed` confirms this behavior explicitly: even when one of two L1 handler transactions returns `Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress))` from the executor, **both** transaction hashes end up in `consumed_l1_handler_tx_hashes`: [5](#0-4) 

This differs from the intended Starknet semantics captured elsewhere in the codebase, where only transactions that actually reached the L1-handler entry-point execution (and had their consumption written into `outputs.messages_to_l2` via `consume_l1_to_l2_message` in the OS) should be considered consumed; the Cairo OS code explicitly skips `consume_l1_to_l2_message` for transactions determined to be reverted before execution: [6](#0-5) 

The batcher-level bookkeeping, however, does not mirror this distinction for the `Err` (hard failure / excluded-from-block) case — it always marks the L1 handler as consumed as soon as it is fed into the executor, independent of whether the executor actually accepted and included it in the block.

### Impact Explanation
An L1-to-L2 message (e.g., a bridge deposit or any L1-triggered contract call carrying value/state effects) whose corresponding L1 handler transaction fails at the executor level (state error, out-of-range address, or any other condition that returns `Err` from the blockifier executor rather than a reverted-but-included execution) is reported to the `apollo_l1_events` provider as consumed. Once consumed, the transaction manager purges the record after the timelock and the message can never be retried, re-proposed, or cancelled through the normal L1 cancellation flow tracked by this component. Since the underlying contract logic (e.g., minting/crediting bridged funds) never actually ran, this results in permanent freezing/loss of the value associated with that L1→L2 message — analogous to the reported bridge-adapter issue where a failed callback left funds unrecoverable because the system already considered the transfer "handled."

### Likelihood Explanation
Any condition causing the blockifier's `TransactionExecutorResult` for an L1 handler to be `Err` rather than `Ok` (e.g., transient state errors, contract address issues, executor internal errors under concurrency, or bouncer/resource exclusion during block building) triggers this path. This does not require a malicious operator or proposer — it can occur during ordinary, honest block building whenever an L1 handler transaction cannot be successfully processed by the executor for reasons independent of the L1 message's own calldata validity.

### Recommendation
Only insert an L1 handler transaction's hash into `consumed_l1_handler_tx_hashes` when the executor result is `Ok` (i.e., the transaction was actually included/executed in the block, whether it reverted or not). For the `Err` branch, exclude such L1 handler hashes from `consumed_l1_handler_tx_hashes` (they are already tracked in `rejected_tx_hashes`), so the L1 events provider keeps them retryable/cancellable rather than permanently discarding the associated L1→L2 message.

### Proof of Concept
The existing test `failed_l1_handler_transaction_consumed` in `crates/apollo_batcher/src/block_builder_test.rs` (lines 1064-1111) already demonstrates the flaw: it feeds two L1 handler transactions, makes the executor return `Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress))` for the first and `Ok` for the second, and asserts (as expected, current behavior) that `result_block_artifacts.execution_data.consumed_l1_handler_tx_hashes` contains *both* transaction hashes — including the one whose execution hard-failed and was never actually applied to the block.

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L643-649)
```rust
        // Insert the tx_hash into the appropriate collection if it's an L1_Handler transaction.
        if let InternalConsensusTransaction::L1Handler(_) = input_tx {
            let is_new_entry = execution_data.consumed_l1_handler_tx_hashes.insert(tx_hash);
            // Even though this doesn't get past the set insertion, this indicates a major, possibly
            // reorg-producing bug, either in some batcher cache or the l1 provider.
            assert!(is_new_entry, "Duplicate L1 handler transaction hash: {tx_hash}.");
        }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L651-717)
```rust
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
                let (tx_index, duplicate_tx_hash) =
                    execution_data.execution_infos_and_signatures.insert_full(
                        tx_hash,
                        (tx_execution_info, input_tx.tx_signature_for_commitment()),
                    );
                assert_eq!(duplicate_tx_hash, None, "Duplicate transaction: {tx_hash}.");

                if let Some(block_number) = proof_facts_block_number(input_tx) {
                    execution_data.proof_facts_block_numbers.insert(tx_hash, block_number);
                }

                // Skip sending the pre confirmed executed transactions, receipts and state diffs
                // during validation flow or if the channel was closed. In validate flow
                // pre_confirmed_tx_sender is None.
                if let Some(pre_confirmed_sender) = pre_confirmed_tx_sender {
                    let tx_receipt = StarknetClientTransactionReceipt::from((
                        tx_hash,
                        TransactionOffsetInBlock(tx_index),
                        // TODO(noamsp): Consider using tx_execution_info and moving the line that
                        // consumes it below this (if it doesn't change functionality).
                        &execution_data.execution_infos_and_signatures[&tx_hash].0,
                        optional_l1_handler_tx,
                    ));

                    let tx_state_diff = StarknetClientStateDiff::from(state_maps).0;

                    let result = pre_confirmed_sender.try_send((
                        input_tx.clone(),
                        tx_receipt,
                        tx_state_diff,
                    ));

                    match result {
                        Ok(_) => {}
                        Err(TrySendError::Closed(_)) => {
                            warn!(
                                "Preconfirmed block writer channel was closed. Skipping to send \
                                 further preconfirmed transactions."
                            );
                            *pre_confirmed_tx_sender = None;
                        }
                        Err(err) => {
                            warn!("Sending data to preconfirmed block writer failed: {:?}", err);
                        }
                    }
                }
            }
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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L111-141)
```rust
    /// Mark a transaction as consumed on L1.
    /// The timestamp is the L1 block timestamp where this tx was marked consumed.
    /// If tx was not already consumed (expected result), return None.
    /// If tx was already consumed (double consumption), return the time when it was previously
    /// consumed. Note that double consumption is a bug.
    pub fn mark_consumed(&mut self, timestamp: BlockTimestamp) -> Option<BlockTimestamp> {
        if self.is_committed() {
            debug!("Marking a committed transaction {} as consumed.", self.tx.tx_hash());
        } else {
            // TODO(guyn): check if this situation should be an error.
            // TODO(guyn): check other state combinations that may be worth an error/warning/debug
            // log.
            debug!(
                "Marking a non-committed transaction {} as consumed. Previous state: {:?}",
                self.tx.tx_hash(),
                self.state
            );
        }
        self.state = TransactionState::Consumed;
        // First check if the tx was already consumed. Double consumption is a bug!
        // If None, it wasn't previously consumed: mark the time and return None to signal
        // everything is ok. If Some, it was already consumed: report the time when it was
        // previously consumed, the caller decides what to do.
        match self.consumed_at {
            Some(existing) => Some(existing),
            None => {
                self.consumed_at = Some(timestamp);
                None
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L247-272)
```rust
    pub fn consume_tx(
        &mut self,
        tx_hash: TransactionHash,
        consumed_at: BlockTimestamp,
        unix_now: u64,
    ) -> Result<(), BlockTimestamp> {
        self.clear_old_tx_from_consumed_queue(unix_now);

        let Some(record) = self.records.get(&tx_hash) else {
            debug!(
                "Attempted to consume an unknown transaction: {tx_hash}. This can happen if the \
                 transaction was too old to be scraped (e.g. it was created before we started \
                 scraping)."
            );
            return Ok(());
        };

        // Double consumption is a bug.
        if let Some(previously_consumed_at) = record.get_consumed_at_timestamp() {
            return Err(previously_consumed_at);
        }

        // Mark the transaction as consumed.
        self.with_record(tx_hash, |record| record.mark_consumed(consumed_at));
        Ok(())
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-390)
```text
    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
```
