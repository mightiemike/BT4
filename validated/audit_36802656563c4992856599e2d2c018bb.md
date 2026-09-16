### Title
Panic on committing a hard-failing L1Handler transaction crashes the L1 Events Provider and halts block confirmation - ([File: crates/apollo_l1_events/src/transaction_manager.rs])

### Summary
An L1Handler transaction whose execution returns `Err` (a hard execution error, e.g. `StateError::OutOfRangeContractAddress`) is simultaneously placed in both `consumed_l1_handler_tx_hashes` and `rejected_tx_hashes` during block building. When the batcher commits the block, `TransactionManager::commit_txs` first marks the tx `Committed` (from the committed/consumed set) and then attempts to mark the *same* tx `Rejected` (from the rejected set), which trips a hard `assert!` and panics the process.

### Finding Description
In `collect_execution_results_and_stream_txs` [1](#0-0) , every `L1Handler` transaction is unconditionally inserted into `execution_data.consumed_l1_handler_tx_hashes`, *before* the execution result is inspected. If the execution result is `Err(...)`, the same tx hash is *also* inserted into `execution_data.rejected_tx_hashes` [2](#0-1) . This dual bookkeeping is exercised directly by the existing unit test `failed_l1_handler_transaction_consumed`, which feeds an `Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress))` result for an L1Handler tx and asserts it still ends up in `consumed_l1_handler_tx_hashes` [3](#0-2) .

When the block is committed, `Batcher::commit_proposal_and_block` filters `rejected_tx_hashes` down to those that are also L1Handler-consumed hashes, and passes both the (unfiltered) consumed set and this filtered rejected set to the L1 Events Provider's `commit_block`: [4](#0-3) 

Because the failing tx hash is a member of both `consumed_l1_handler_tx_hashes` and the filtered `rejected_l1_handler_tx_hashes`, it is passed as both `committed_txs` and `rejected_txs` to `TransactionManager::commit_txs`: [5](#0-4) 

`commit_txs` first iterates `committed_txs`, calling `mark_committed()`, which sets `self.committed = true` and `state = Committed`: [6](#0-5) 

It then iterates `rejected_txs` and calls `mark_rejected()` on the *same* tx hash, which asserts `!self.committed`: [7](#0-6) 

Since `self.committed` was just set to `true` by the prior loop, this assertion fails and the process panics ("Attempted to reject a committed transaction {tx_hash}"). This directly contradicts the documented intended behavior in the L1-handler flow diagram, which states that on commit, "For rejected txs: Unstage, keep as Pending" [8](#0-7)  — i.e., the design intent was that a rejected L1Handler tx should remain retryable/`Pending`, not conflict with a simultaneously-committed state.

### Impact Explanation
This is deterministically reachable by any account/contract that triggers, via an `L1->L2` message, an L1Handler call that causes a hard execution `Err` during block building (e.g., targeting an invalid/out-of-range contract address, or other `StateError`/`TransactionExecutorError` conditions that are not simple reverts). Since all honest sequencer nodes execute the same transaction deterministically when building or validating the same block, every node that reaches `commit_proposal_and_block`/`commit_txs` for that block will hit the same panic. This crashes the L1 Events Provider component (and, since the batcher's commit flow depends on a successful response from it, effectively halts block commitment) on every node that processes this L1 message, which is a full liveness failure: the network becomes unable to confirm new transactions/blocks until operators intervene. This qualifies as a valid, reachable, node-crashing consensus-halting bug (not a mere resource/DoS-only or malicious-operator issue): it is triggered by a single ordinary L1 message from an unprivileged sender.

### Likelihood Explanation
Likelihood is high: any user with the ability to send an `L1->L2` message (a standard, unprivileged action available to any L1 account) can construct calldata/target contract that causes a hard execution error (not a soft revert) for the invoked L1Handler entry point. The existing repository test (`failed_l1_handler_transaction_consumed`) demonstrates that such hard-`Err` results are a normal, already-anticipated code path for L1Handler execution, making the trigger condition realistic rather than purely theoretical.

### Recommendation
- Ensure a given transaction hash cannot appear in both `committed_txs` and `rejected_txs` passed into `TransactionManager::commit_txs`, or make `commit_txs`/`mark_rejected` tolerant of this overlap by defining clear precedence (e.g., a hard-failing but included L1Handler tx should always be treated as "committed" for L1-consumption purposes, since the message was in fact consumed once included in a block, and never additionally marked "rejected").
- Align the implementation with the documented intent in the flow diagram ("rejected txs stay Pending") — i.e., decide definitively whether a hard-failing L1Handler tx is consumed (and thus `Committed`) or should be retried (`Pending`), and remove the ability for `rejected_l1_handler_tx_hashes` to include hashes that are also unconditionally added to `consumed_l1_handler_tx_hashes`.
- Add a regression test that exercises `Batcher::commit_proposal_and_block` (or `TransactionManager::commit_txs` directly) with a tx hash present in both the committed and rejected sets, verifying no panic occurs and that the resulting state is well-defined.

### Proof of Concept
1. Submit an `L1->L2` message whose L1Handler entry point call triggers a hard execution error during block building (e.g. an invalid/out-of-range target contract address), reproducing the exact scenario in `failed_l1_handler_transaction_consumed` [3](#0-2) .
2. `collect_execution_results_and_stream_txs` inserts the tx hash into both `consumed_l1_handler_tx_hashes` and `rejected_tx_hashes` [9](#0-8) .
3. The block is closed and committed; `Batcher::commit_proposal_and_block` computes `rejected_l1_handler_tx_hashes` (which will include this tx hash, since it is present in `consumed_l1_handler_tx_hashes`) and calls `l1_events_provider_client.commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)` [4](#0-3) .
4. Inside `TransactionManager::commit_txs`, the tx hash is first marked `Committed` and then `mark_rejected()` is invoked on it, tripping `assert!(!self.committed, ...)` and panicking the process [5](#0-4) [7](#0-6) .
5. Because this happens deterministically for every honest node processing the same block, the entire network stalls on this height.

### Citations

**File:** crates/apollo_batcher/src/block_builder.rs (L633-708)
```rust
        let tx_hash = input_tx.tx_hash();

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
```

**File:** crates/apollo_batcher/src/block_builder_test.rs (L1079-1128)
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
            #[cfg(feature = "os_input")]
            initial_reads: test_initial_reads(),
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

**File:** crates/apollo_batcher/src/batcher.rs (L1111-1121)
```rust
        // Notify the L1 provider of the new block.
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_events_provider_result = self
            .l1_events_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
            .await;
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L147-166)
```rust
    pub fn commit_txs(
        &mut self,
        committed_txs: &[TransactionHash],
        rejected_txs: &[TransactionHash],
    ) {
        self.rollback_staging();

        for &tx_hash in committed_txs {
            self.create_record_if_not_exist(tx_hash);
            self.with_record(tx_hash, |r| r.mark_committed()).unwrap();
        }
        for &tx_hash in rejected_txs {
            self.with_record(tx_hash, |r| r.mark_rejected()).expect(
                "Rejected L1 handler tx has no record. Unreachable: all L1 handler txs in a \
                 committed block were validated as known (validation rejects unknown hashes), \
                 sync commits with empty rejected_txs, and records are only removed via L1 \
                 cancellation/consumption, which can't race a block.",
            );
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L50-60)
```rust
    pub fn mark_committed(&mut self) {
        // Can't return error because committing only part of a block leaves the provider in an
        // undetermined state.
        assert!(
            !self.committed,
            "L1 handler transaction {} committed twice, this may lead to l2 reorgs,",
            self.tx.tx_hash()
        );
        self.state = TransactionState::Committed;
        self.committed = true;
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L62-72)
```rust
    // Note: double reject not currently checked.
    pub fn mark_rejected(&mut self) {
        // Pedantic, this is unlikely to happen.
        assert!(
            !self.committed,
            "Attempted to reject a committed transaction {}",
            self.tx.tx_hash()
        );
        self.state = TransactionState::Rejected;
        self.rejected = true;
    }
```

**File:** docs/diagrams/06-l1-handler-flow.md (L178-186)
```markdown

        Note over TxMgr: For committed txs: Pending to Committed
        Note over TxMgr: For rejected txs: Unstage, keep as Pending

        TxMgr->>TxMgr: rollback_staging()
        Note over TxMgr: Increments staging epoch<br/>(unstages all txs for next block)
        L1P->>L1P: increment current_height
        L1P-->>B: Ok
    end
```
