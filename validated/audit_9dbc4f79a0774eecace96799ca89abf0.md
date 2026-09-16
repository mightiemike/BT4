## Title
Single invalid L1Handler transaction in a batch aborts the entire block validation, allowing a griefing/liveness DoS analogous to the "heal" front-running batch-revert bug - (File: `crates/apollo_batcher/src/transaction_provider.rs`)

### Summary
`ValidateTransactionProvider::get_txs` processes a batch of transactions received from a proposer during consensus validation. When it encounters an `InternalConsensusTransaction::L1Handler`, it calls `l1_events_provider_client.validate(...)` for that single transaction. If *any single* L1Handler transaction in the batch is found `Invalid` (e.g. `ConsumedOnL1`, `AlreadyIncludedOnL2`, `AlreadyIncludedInProposedBlock`, `NotFound`), the function immediately returns an `Err`, discarding the whole batch instead of skipping only the offending transaction. This mirrors the reported `heal()` bug class: a batch operation performs a per-item status check and reverts/fails the *entire* batch because one item's status changed, rather than tolerating/skipping the single stale item.

### Finding Description
In `get_txs`, the loop iterates over the whole received buffer of transactions and, for each `L1Handler` transaction, checks its L1 validation status: [1](#0-0) 

If the status returned by `l1_events_provider_client.validate` is `Invalid`, the function returns `Err(TransactionProviderError::L1HandlerTransactionValidationFailed {...})` for the *entire* batch, even though the batch may also contain many other perfectly valid transactions (L1Handler or otherwise).

The underlying per-transaction status check is implemented in `TransactionManager::validate_tx`, which can return `AlreadyIncludedOnL2`, `CancelledOnL2`, `ConsumedOnL1`, or `AlreadyIncludedInProposedBlock` depending on races with L1 consumption/cancellation events or staging by a concurrent proposal: [2](#0-1) 

This single-item failure propagates up through the batcher, causing the whole build/validation attempt to fail-fast, as shown by the documented flow and existing tests: [3](#0-2) [4](#0-3) 

The consensus-side caller (`apollo_consensus_orchestrator`) forwards whole `TransactionBatch`es to the batcher for validation; a single-tx failure inside that batch leads to the whole proposal round being marked failed: [5](#0-4) 

The intended design (per the flow docs) is explicit: any invalid status for a single L1Handler tx should "fail block validation" for the batch: [6](#0-5) 

### Impact Explanation
`ConsumedOnL1` and `CancelledOnL2` statuses are driven by L1 message consumption/cancellation timing, which is controllable by an unprivileged L1 message sender/consumer (anyone can call the L1 bridge/consumer contract to consume or request cancellation of their own L1→L2 message). By timing the L1-side consumption/cancellation of a single message to land just before a validator processes a proposal containing that L1Handler transaction, an attacker can force the entire batch's validation to fail — rejecting a proposal that otherwise contains many legitimate transactions from unrelated senders. Repeated at each round, this becomes a griefing/liveness degradation on block validation similar in kind to the reported `heal()` issue (loss of validator/proposer work and wasted round(s), forcing repeated re-proposal), rather than a fully isolated per-transaction failure as would be expected from a well-isolated batch validator.

### Likelihood Explanation
The attacker only needs control of the timing of their own L1 message consumption/cancellation (a normal, unprivileged L1 action) relative to when their L1Handler transaction happens to be bundled into a proposer's batch — a race condition inherent to the mempool/L1-events-provider design, not requiring any special privilege, and repeatable across rounds.

### Recommendation
Change `ValidateTransactionProvider::get_txs` (and any similar per-batch validators driven by `L1EventsProviderClient::validate`) to skip/exclude the single invalid L1Handler transaction from the batch (treating it like a normal "this tx is unincludable" case, consistent with how `commit_proposal_and_block` already separately tracks `rejected_l1_handler_tx_hashes`), instead of aborting validation for the whole batch on the first invalid status. Only truly systemic errors (e.g., provider-level `L1EventsProviderError`) should fail the batch as a whole.

### Proof of Concept
1. Proposer builds a block/proposal batch containing `N` transactions, including one `L1Handler` tx `H` originating from an attacker-controlled L1 message and `N-1` unrelated valid transactions.
2. Attacker, watching the L1 bridge/mempool, triggers consumption (or cancellation) of the L1 message underlying `H` on L1 just before validators process the proposal.
3. Validating node's `ValidateTransactionProvider::get_txs` receives the batch (`crates/apollo_batcher/src/transaction_provider.rs:187-224`), calls `validate()` for `H`, gets `Invalid(ConsumedOnL1)` from `TransactionManager::validate_tx` (`crates/apollo_l1_events/src/transaction_manager.rs:116-145`), and returns `Err(L1HandlerTransactionValidationFailed)` for the whole batch.
4. `HandledProposalPart::Failed` is produced in `validate_proposal.rs`, and the entire proposal — including the `N-1` valid, unrelated transactions — is rejected, exactly analogous to Alice's 20-agent heal batch reverting because Bob front-ran one agent's status.

### Citations

**File:** crates/apollo_batcher/src/transaction_provider.rs (L198-223)
```rust
        for tx in &buffer {
            if let InternalConsensusTransaction::L1Handler(tx) = tx {
                let l1_validation_status = self
                    .l1_events_provider_client
                    .validate(tx.tx_hash, self.height)
                    .await
                    .inspect_err(|err| {
                        warn!(
                            "L1 provider error while validating L1 handler transaction: {:?}",
                            err
                        );
                        BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
                    })
                    .unwrap_or(L1ValidationStatus::Invalid(
                        L1InvalidValidationStatus::L1EventsProviderError,
                    ));
                if let L1ValidationStatus::Invalid(validation_status) = l1_validation_status {
                    return Err(TransactionProviderError::L1HandlerTransactionValidationFailed {
                        tx_hash: tx.tx_hash,
                        validation_status,
                    });
                }
                continue;
            }
        }
        Ok(buffer)
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L116-145)
```rust
    pub fn validate_tx(&mut self, tx_hash: TransactionHash, unix_now: u64) -> ValidationStatus {
        let current_staging_epoch_cloned = self.current_staging_epoch;

        let policy = TransactionRecordPolicy {
            cancellation_timelock: self.config.l1_handler_cancellation_timelock_seconds,
        };

        let validation_status = self.with_record(tx_hash, |record| {
            // If the current time affects the state, update state now.
            record.update_time_based_state(unix_now, policy);
            if !record.is_validatable() {
                match record.state {
                    TransactionState::Committed => {
                        InvalidValidationStatus::AlreadyIncludedOnL2.into()
                    }
                    TransactionState::CancelledOnL2 => {
                        InvalidValidationStatus::CancelledOnL2.into()
                    }
                    TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1.into(),
                    _ => unreachable!(),
                }
            } else if record.try_mark_staged(current_staging_epoch_cloned) {
                ValidationStatus::Validated
            } else {
                InvalidValidationStatus::AlreadyIncludedInProposedBlock.into()
            }
        });

        validation_status.unwrap_or(InvalidValidationStatus::NotFound.into())
    }
```

**File:** crates/apollo_batcher/src/transaction_provider_test.rs (L265-298)
```rust
#[rstest]
#[tokio::test]
async fn validate_fails(
    mut mock_dependencies: MockDependencies,
    #[values(
        InvalidValidationStatus::AlreadyIncludedInProposedBlock,
        InvalidValidationStatus::AlreadyIncludedOnL2,
        InvalidValidationStatus::ConsumedOnL1,
        InvalidValidationStatus::NotFound
    )]
    expected_validation_status: InvalidValidationStatus,
) {
    let test_tx = test_l1handler_tx();
    mock_dependencies.expect_validate_l1handler(
        test_tx.clone(),
        L1ValidationStatus::Invalid(expected_validation_status),
    );
    mock_dependencies
        .simulate_input_txs(vec![
            InternalConsensusTransaction::L1Handler(test_tx),
            InternalConsensusTransaction::RpcTransaction(internal_invoke_tx(
                InvokeTxArgs::default(),
            )),
        ])
        .await;
    let mut validate_tx_provider = mock_dependencies.validate_tx_provider();

    let result = validate_tx_provider.get_txs(MAX_TXS_PER_FETCH).await;
    assert_matches!(
        result,
        Err(TransactionProviderError::L1HandlerTransactionValidationFailed { validation_status, .. })
        if validation_status == expected_validation_status
    );
}
```

**File:** crates/apollo_batcher/src/block_builder_test.rs (L884-923)
```rust
#[rstest]
#[case::already_included_in_proposed_block(AlreadyIncludedInProposedBlock)]
#[case::already_included_on_l2(AlreadyIncludedOnL2)]
#[case::consumed_on_l1(ConsumedOnL1)]
#[case::not_found(NotFound)]
#[tokio::test]
async fn test_validate_block_l1_handler_validation_error(#[case] status: InvalidValidationStatus) {
    let tx_provider = mock_tx_provider_with_error(L1HandlerTransactionValidationFailed {
        tx_hash: tx_hash!(0),
        validation_status: status,
    });

    let (_abort_sender, abort_receiver) = tokio::sync::oneshot::channel();

    let mut helper = ExpectationHelper::new();
    helper.deadline_expectations();

    helper.mock_transaction_executor.expect_abort_block().times(1).return_once(|| ());

    let result = run_build_block(
        helper.mock_transaction_executor,
        tx_provider,
        None,
        true,
        abort_receiver,
        BLOCK_GENERATION_DEADLINE_SECS,
        DEFAULT_IDLE_TIMEOUT_MS,
    )
    .await;

    assert_matches!(
        result,
        Err(BlockBuilderError::FailOnError(
            FailOnErrorCause::L1HandlerTransactionValidationFailed(
                TransactionProviderError::L1HandlerTransactionValidationFailed { .. }
            )
        )),
        "Expected FailOnError for validation status: {status:?}"
    );
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-612)
```rust
            content.push(txs.clone());
            let input = SendTxsForProposalInput { proposal_id, txs };
            let response = match batcher.send_txs_for_proposal(input).await {
                Ok(response) => response,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to send transactions to batcher: {e:?}"
                    ));
                }
            };
            match response {
                SendTxsForProposalStatus::Processing => HandledProposalPart::Continue,
                SendTxsForProposalStatus::InvalidProposal(err) => HandledProposalPart::Invalid(err),
            }
        }
```

**File:** docs/diagrams/06-l1-handler-flow.md (L142-149)
```markdown
            opt Validated
                TxMgr->>TxMgr: mark_staged(tx_hash)
                TxProv->>BF: add_txs_to_block([tx])
            end

            opt Invalid (AlreadyIncludedOnL2, CancelledOnL2, ConsumedOnL1, NotFound, AlreadyIncludedInProposedBlock, L1EventsProviderError)
                Note over TxProv: Fail block validation
            end
```
