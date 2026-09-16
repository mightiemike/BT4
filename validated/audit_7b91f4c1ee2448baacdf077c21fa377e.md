### Title
L1 handler cancellation timelock relies on each node's local wall clock instead of a deterministic block/consensus timestamp, letting node stalls or clock skew desynchronize validation outcomes across honest sequencers - ([File: crates/apollo_l1_events/src/transaction_manager.rs])

### Summary
The `TelcoinDistributor` bug class is: a time-window security check (`challengePeriod`) is measured against `block.timestamp`/wall time that can silently keep advancing while the system is "paused," so by the time normal operation resumes the window has already elapsed and the protection is bypassed. The `apollo_l1_events` L1 handler cancellation mechanism has the same root defect: the "protection window" that keeps a cancellation-requested L1 handler transaction includable/validatable (`l1_handler_cancellation_timelock_seconds`) is evaluated against each node's own OS wall clock (`Clock::unix_now()`) rather than a canonical, consensus-derived timestamp.

### Finding Description
When a cancellation is requested on L1 for a pending `L1HandlerTransaction`, the transaction is marked `CancellationStartedOnL2` with the L1 `block_timestamp` at which the request was observed [1](#0-0) . The record still remains includable/validatable until the cancellation timelock elapses; the expiry check is:

```rust
pub fn update_time_based_state(&mut self, unix_now: u64, policy: TransactionRecordPolicy) {
    ...
    let is_cancellation_timelock_passed =
        unix_now >= *requested_at.saturating_add(cancellation_timelock);
    if is_cancellation_timelock_passed {
        self.state = TransactionState::CancelledOnL2;
    }
}
``` [2](#0-1) 

The `unix_now` fed into this check comes directly from `self.clock.unix_now()` at the moment `validate()` (called during proposal validation) or `get_txs()` (called during proposal building) executes on each individual node:

```rust
pub fn validate(&mut self, tx_hash: TransactionHash, height: BlockNumber) -> ... {
    ...
    ProviderState::Validate => Ok(self.tx_manager.validate_tx(tx_hash, self.clock.unix_now())),
``` [3](#0-2) 

and

```rust
ProviderState::Propose => {
    let txs = self.tx_manager.get_txs(n_txs, self.clock.unix_now());
``` [4](#0-3) 

This is not the deterministic proposal/block timestamp used elsewhere in consensus validation (e.g., `block_timestamp_window_seconds` checks against the proposal's own timestamp field); it is each node's independent real-time clock, sampled at whatever moment that node happens to process the request. There is no mechanism accounting for a node being delayed, stalled, or stuck in `CatchingUp` state (which can persist for an unbounded amount of time while `Catchupper` retries state-sync fetches) [5](#0-4) . If a validating node's clock/processing is delayed relative to the proposer (analogous to the paused `TelcoinDistributor` scenario), the cancellation window that was still open when the proposer built the block may have already expired by the time that node evaluates `validate_tx`, producing a different (`CancelledOnL2`) verdict for the same transaction than what the honest proposer and other validators computed.

### Impact Explanation
Because inclusion validity of an `L1HandlerTransaction` is derived from a non-deterministic, node-local wall clock rather than a value embedded in the block/proposal, honest nodes can diverge on whether a given L1 handler transaction is valid to include. A node that was delayed (e.g., recovering from a stall, prolonged `CatchingUp`, or simple clock skew) can reject as `CancelledOnL2` a transaction that the proposer and other on-time validators correctly accepted, causing that node to fail block validation for an otherwise valid proposal. At scale this is a consensus-safety/liveness hazard: enough diverging validators can prevent a proposal from reaching quorum, stalling block confirmation, matching the "network unable to confirm new transactions" / "honest-node divergence" impact classes.

### Likelihood Explanation
This requires no privileged access: any L1 message sender can trigger an L1 handler transaction and (via a cancellation request on the L1 message contract) start the cancellation-timelock race condition described here. The divergence is more likely to manifest under real-world conditions such as node restarts, catch-up after downtime, GC/IO stalls, or ordinary clock drift between machines — none of which require a malicious operator, node, or peer, satisfying the "unprivileged L1 message sender" reachability requirement.

### Recommendation
Replace the wall-clock-based `unix_now()` timestamp used in `update_time_based_state`/`validate_tx`/`get_txs` with a deterministic timestamp tied to the proposal/block being validated (e.g., the block timestamp under validation, or the proposer's declared timestamp already checked by `block_timestamp_window_seconds`), so that all honest nodes evaluating the same proposal compute identical cancellation-timelock expiry results regardless of their own processing delay or clock skew.

### Proof of Concept
1. An L1 handler message is scraped and becomes `Pending`/proposable via `add_tx` [6](#0-5) .
2. A cancellation is requested on L1 close to the block's proposal time; `request_cancellation` records `cancellation_requested_at` [7](#0-6) .
3. The proposer calls `get_txs`, whose `self.clock.unix_now()` is still before `requested_at + cancellation_timelock`, so the tx is included in the proposal.
4. A validator that was stalled/catching up (or simply has clock skew) processes `validate()` later; its `self.clock.unix_now()` is now past `requested_at + cancellation_timelock`, so `update_time_based_state` flips the record to `CancelledOnL2` and `validate_tx` returns `InvalidValidationStatus::CancelledOnL2` [8](#0-7) , causing that honest validator to reject a proposal accepted by the proposer and other on-time validators — the timing-based race exercised directly by the existing test `new_l1_handler_tx_propose_validate_cancellation_timelock` [9](#0-8) , but here without a synchronized clock across nodes.

### Citations

**File:** crates/apollo_l1_events/src/transaction_record.rs (L74-101)
```rust
    /// Mark a cancellation request for this transaction.
    /// Returns the existing cancellation timestamp if one exists, `None` if this is the first
    /// request.
    pub fn mark_cancellation_request(
        &mut self,
        timestamp: BlockTimestamp,
    ) -> Option<BlockTimestamp> {
        let tx_hash = self.tx.tx_hash();
        // Once committed on L2, cancellation requests are only recorded for debugging purposes, but
        // not processed.
        if self.is_committed() {
            warn!(
                "L1 handler transaction {tx_hash} was not marked for cancellation started on L2 \
                 as it is already committed."
            )
        } else {
            info!("Marking L1 handler transaction {tx_hash} as cancellation started on L2.");
            self.state = TransactionState::CancellationStartedOnL2;
        }

        match self.cancellation_requested_at {
            Some(existing) => Some(existing),
            None => {
                self.cancellation_requested_at = Some(timestamp);
                None
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L191-208)
```rust
    /// Update the state of the record based on the current time and policy.
    /// This updates the state based on time-based state transitions, such as moving from
    /// CancellationStartedOnL2 to CancelledOnL2 after the timelock expires.
    pub fn update_time_based_state(&mut self, unix_now: u64, policy: TransactionRecordPolicy) {
        if let Some(requested_at) = self.cancellation_requested_at {
            if self.committed {
                return; // Committing overrides cancellations.
            }

            let cancellation_timelock = &policy.cancellation_timelock.as_secs();
            let is_cancellation_timelock_passed =
                unix_now >= *requested_at.saturating_add(cancellation_timelock);

            if is_cancellation_timelock_passed {
                self.state = TransactionState::CancelledOnL2;
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L216-252)
```rust
    pub fn get_txs(
        &mut self,
        n_txs: usize,
        height: BlockNumber,
    ) -> L1EventsProviderResult<Vec<L1HandlerTransaction>> {
        if self.state.is_uninitialized() {
            return Err(L1EventsProviderError::Uninitialized);
        }

        self.check_height_with_error(height)?;

        match self.state {
            ProviderState::Propose => {
                let txs = self.tx_manager.get_txs(n_txs, self.clock.unix_now());
                info!(
                    "Returned {} out of {} transactions, ready for sequencing.",
                    txs.len(),
                    n_txs
                );
                debug!(
                    "Returned L1Handler txs: {:?}",
                    txs.iter()
                        .map(|tx| format!(
                            "L2 tx hash: {}, L1-L2 msg hash: {}",
                            tx.tx_hash,
                            tx.tx.calc_msg_hash()
                        ))
                        .collect::<Vec<_>>()
                );
                Ok(txs)
            }
            _ => Err(L1EventsProviderError::UnexpectedProviderState {
                expected: ProviderState::Propose,
                found: self.state,
            }),
        }
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L257-277)
```rust
    #[instrument(skip(self), err)]
    pub fn validate(
        &mut self,
        tx_hash: TransactionHash,
        height: BlockNumber,
    ) -> L1EventsProviderResult<ValidationStatus> {
        if self.state.is_uninitialized() {
            return Err(L1EventsProviderError::Uninitialized);
        }

        self.check_height_with_error(height)?;
        match self.state {
            ProviderState::Validate => {
                Ok(self.tx_manager.validate_tx(tx_hash, self.clock.unix_now()))
            }
            _ => Err(L1EventsProviderError::UnexpectedProviderState {
                expected: ProviderState::Validate,
                found: self.state,
            }),
        }
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L383-482)
```rust
    /// Any commit_block call gets rerouted to this function when in CatchingUp state.
    /// - If block number is higher than current height, block is backlogged.
    /// - If provider gets a block consistent with current_height, apply it and then the rest of the
    ///   backlog, then transition to Pending state.
    /// - Blocks lower than current height are checked for consistency with existing transactions.
    fn accept_commit_while_catching_up(
        &mut self,
        committed_txs: IndexSet<TransactionHash>,
        new_height: BlockNumber,
    ) -> L1EventsProviderResult<()> {
        let current_height = self.current_height;
        debug!(
            "Catchupper processing commit-block at height: {new_height}, current height is \
             {current_height}"
        );
        match new_height.cmp(&current_height) {
            // This is likely a bug in the batcher/sync, it should never be _behind_ the provider.
            Less => {
                // TODO(guyn): check if this is reliable: old blocks can have txs that were
                // committed then consumed and deleted. We should probably decide to always log and
                // ignore old blocks or always return an error.
                let diff_from_already_committed: Vec<_> = committed_txs
                    .iter()
                    .copied()
                    .filter(|&tx_hash| !self.tx_manager.is_committed(tx_hash))
                    .collect();

                if diff_from_already_committed.is_empty() {
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, and all committed transaction hashes already known to be \
                         committed."
                    );
                    return Ok(());
                } else {
                    // This is either a configuration error or a bug in the
                    // batcher/sync/catching up code.
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, with DIFFERENT transaction_hashes: \
                         {diff_from_already_committed:?}"
                    );
                    Err(L1EventsProviderError::UnexpectedHeight {
                        expected_height: current_height,
                        got: new_height,
                    })?
                }
            }
            // TODO(guyn): check what about rejected txs here and in the backlog?
            Equal => self.apply_commit_block(committed_txs, Default::default()),
            // We're still syncing, backlog it, it'll get applied later.
            Greater => {
                self.catchupper.add_commit_block_to_backlog(committed_txs, new_height);
                // No need to check the backlog or catchup completion, since those are only
                // applicable if we just increased the provider's height, like in the `Equal` case.
                return Ok(());
            }
        };

        // If caught up, apply the backlog and transition to Pending.
        // Note that at this point self.current_height is already incremented to the next height, it
        // is one more than the latest block that was committed.
        if self.catchupper.is_caught_up(self.current_height) {
            info!(
                "Catch up sync completed, provider height is now {}, processing backlog...",
                self.current_height
            );
            let backlog = std::mem::take(&mut self.catchupper.commit_block_backlog);
            assert!(
                backlog.is_empty()
                    || self.current_height == backlog.first().unwrap().height
                        && backlog
                            .windows(2)
                            .all(|height| height[1].height == height[0].height.unchecked_next()),
                "Backlog must have sequential heights starting sequentially after current height: \
                 {}, backlog: {:?}",
                self.current_height,
                backlog.iter().map(|commit_block| commit_block.height).collect::<Vec<_>>()
            );

            info!(
                "Applying commit-block backlog for heights: {:?}",
                backlog.iter().map(|commit_block| commit_block.height).collect::<Vec<_>>()
            );

            for committed_block in backlog {
                self.apply_commit_block(committed_block.committed_txs, Default::default());
            }

            info!(
                "Catch up done: commit-block backlog was processed, now transitioning to Pending \
                 state at new height: {}.",
                self.current_height
            );

            self.state = ProviderState::Pending;
        }

        Ok(())
    }
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L173-209)
```rust
    pub fn add_tx(
        &mut self,
        tx: L1HandlerTransaction,
        block_timestamp: BlockTimestamp,
        scrape_timestamp: UnixTimestamp,
    ) {
        let tx_hash = tx.tx_hash;
        // If exists, return false and do nothing. If not, create the record as a HashOnly payload.
        let is_new_record = self.create_record_if_not_exist(tx_hash);
        // Replace a HashOnly payload with a Full payload. Do not update a Full payload.
        // A hash only payload can come from catching up from state sync, and then updated by
        // add_events from the scraper. However, if we get the same full tx twice (from the scraper)
        // it could indicate a double-scrape, and may cause the tx to be re-added to the proposable
        // index.
        self.with_record(tx_hash, move |record| match &record.tx {
            TransactionPayload::HashOnly(_) => {
                if !is_new_record {
                    info!(
                        "Transaction {tx_hash} already exists as a HashOnly payload. It was \
                         probably gotten via state sync component, and is now updated with a Full \
                         payload."
                    );
                }
                record.tx.set(tx, block_timestamp, scrape_timestamp);
                // Counts the HashOnly -> Full transition, regardless of whether the HashOnly
                // was just created here or pre-existed from state sync.
                L1_MESSAGE_SCRAPER_L1_HANDLER_TX_COUNT.increment(1);
            }
            TransactionPayload::Full { tx: _, created_at_block_timestamp: _, scrape_timestamp } => {
                warn!(
                    "Transaction {tx_hash} already exists as a Full payload, scraped at \
                     {scrape_timestamp}. This could indicate a double scrape. Ignoring the new \
                     transaction."
                );
            }
        });
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L211-219)
```rust
    pub fn request_cancellation(
        &mut self,
        tx_hash: TransactionHash,
        block_timestamp: BlockTimestamp,
    ) -> Option<BlockTimestamp> {
        self.with_record(tx_hash, |r| r.mark_cancellation_request(block_timestamp)).expect(
            "Should not be possible to request cancellation for non-existent transaction {tx_hash}",
        )
    }
```

**File:** crates/apollo_l1_events/tests/flow_test_cancellation.rs (L27-93)
```rust
#[tokio::test]
async fn new_l1_handler_tx_propose_validate_cancellation_timelock() {
    // Setup.
    // Setup the base layer.
    let mut base_layer = setup_anvil_base_layer().await;

    let (l2_hash, nonce) = send_message_from_l1_to_l2(&mut base_layer, CALL_DATA).await;

    let l1_events_provider_client =
        setup_scraper_and_provider(base_layer.ethereum_base_layer.clone(), None).await;

    // Test.
    tokio::time::pause();
    let next_block_height = BlockNumber(TARGET_L2_HEIGHT.0 + 1);

    // Check that we can validate this message.
    l1_events_provider_client.start_block(SessionState::Validate, next_block_height).await.unwrap();
    assert_eq!(
        l1_events_provider_client.validate(l2_hash, next_block_height).await.unwrap(),
        ValidationStatus::Validated
    );

    send_cancellation_request(&base_layer, CALL_DATA, nonce).await;

    // Wait for another scraping.
    tokio::time::advance(POLLING_INTERVAL_DURATION + ROUND_TO_SEC_MARGIN_DURATION).await;

    // Keep trying to get the snapshot showing the cancellation
    for _i in 0..1000 {
        let snapshot = l1_events_provider_client.get_l1_events_provider_snapshot().await.unwrap();
        if snapshot.cancellation_started_on_l2.contains(&l2_hash) {
            break;
        }
        tokio::time::sleep(WAIT_FOR_ASYNC_PROCESSING_DURATION).await;
    }

    // Verify we have left the loop with the cancellation marked as started on L2.
    let snapshot = l1_events_provider_client.get_l1_events_provider_snapshot().await.unwrap();
    assert!(snapshot.cancellation_started_on_l2.contains(&l2_hash));
    assert_eq!(snapshot.number_of_txs_in_records, 1);

    // Should still be able to validate.
    l1_events_provider_client.start_block(SessionState::Validate, next_block_height).await.unwrap();
    assert_eq!(
        l1_events_provider_client.validate(l2_hash, next_block_height).await.unwrap(),
        ValidationStatus::Validated
    );

    // Should not be able to propose.
    let n_txs = 1;
    l1_events_provider_client.start_block(SessionState::Propose, next_block_height).await.unwrap();
    let txs = l1_events_provider_client.get_txs(n_txs, next_block_height).await.unwrap();
    assert!(txs.is_empty());

    // Sleep at least one second more than the timelock to make sure we are not failing due to
    // fractional seconds.
    tokio::time::advance(TIMELOCK_DURATION + ROUND_TO_SEC_MARGIN_DURATION).await;

    // Should no longer be able to validate.
    l1_events_provider_client.start_block(SessionState::Validate, next_block_height).await.unwrap();
    assert_eq!(
        l1_events_provider_client.validate(l2_hash, next_block_height).await.unwrap(),
        ValidationStatus::Invalid(InvalidValidationStatus::CancelledOnL2)
    );

    // Still cannot propose.
    l1_events_provider_client.start_block(SessionState::Propose, next_block_height).await.unwrap();
```
