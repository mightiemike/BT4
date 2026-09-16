### Title
Late/out-of-order L1 cancellation-request event resurrects a Committed/Consumed L1 handler transaction, bypassing double-inclusion protection - (File: `crates/apollo_l1_events/src/transaction_record.rs`)

### Summary
`TransactionRecord::mark_cancellation_request` only guards against re-arming a transaction that is in the `Committed` state. It does not check for the `Consumed` (or `Rejected`/`CancelledOnL2`) state before overwriting `self.state` with `CancellationStartedOnL2`. Because `TransactionRecord::mark_consumed` unconditionally overwrites `self.state` from `Committed` to `Consumed` (while leaving the separate `committed: bool` metadata flag untouched), a subsequently-processed `TransactionCancellationStarted` L1 event can flip an already consumed/committed L1-handler transaction's `state` back to `CancellationStartedOnL2`. This corrupts `is_validatable()`/`is_committed()`/`is_consumed()`, which no longer reflect that the transaction was already included and executed on L2.

### Finding Description
The transaction lifecycle is tracked purely via the mutable `state` field: [1](#0-0) 

`mark_consumed` (invoked from `Event::TransactionConsumed` handling) sets `self.state = TransactionState::Consumed` unconditionally, without checking whether the tx was already `Committed`, and without resetting anything that would later block re-arming: [2](#0-1) 

`mark_cancellation_request` (invoked from `Event::TransactionCancellationStarted` handling) only special-cases the `Committed` state: [3](#0-2) 

Since `is_committed()` checks `self.state == TransactionState::Committed`, once `state` has moved on to `Consumed`, `is_committed()` returns `false`, so a cancellation-request event that arrives afterward takes the "else" branch and overwrites `state` back to `CancellationStartedOnL2` — silently discarding the fact that the L1 handler message was already executed on L2.

The dispatcher in `L1EventsProvider::add_events` does no additional state validation before forwarding the event to `request_cancellation`/`mark_cancellation_request` — it only checks that the record exists: [4](#0-3) 

Once corrupted to `CancellationStartedOnL2`, `is_validatable()` (`!is_committed() && !is_cancelled() && !is_consumed()`) now evaluates to `true` for a transaction that has already been executed: [5](#0-4) 

Additionally, `update_time_based_state` — which would normally advance `CancellationStartedOnL2` → `CancelledOnL2` after the timelock — is short-circuited by the *separate* `committed: bool` metadata flag (which was never cleared by `mark_consumed`), so the corrupted record can remain stuck indefinitely in `CancellationStartedOnL2`, permanently marked `is_validatable() == true`: [6](#0-5) 

The state transition is invoked through `TransactionManager::request_cancellation` and `TransactionManager::consume_tx`: [7](#0-6) 

An L1 message sender legitimately controls when to call `startL1ToL2MessageCancellation` for their own message on L1 (an unprivileged, single L1 sender action). Because a message is only actually marked consumed on the L1 `StarknetCore` contract when the containing L2 block's state update is later posted to L1, there is a real window after the sequencer commits the L1-handler tx (and, in the sequencer's tracking, later marks it `Consumed` upon seeing the `ConsumedMessageToL2` event) during which a legitimately-timed (or scraper-reordered) `TransactionCancellationStarted` event can still be delivered and processed by the sequencer, corrupting the record.

### Impact Explanation
`is_validatable()` is the exact predicate the L1 events provider uses to decide "any node can include this transaction in a block" for `validate_tx`. A record incorrectly reporting `is_validatable() == true` for an already-committed/consumed L1 handler transaction breaks the sequencer's double-inclusion / already-consumed protection invariant. This can lead to a validator wrongly returning `Validated` for a transaction that was already executed and had its funds/side effects applied on L2 and already consumed on L1 — enabling re-execution of an L1→L2 message (double mint/double credit of the bridged asset or other L1-handler side effects) and honest-node state divergence, matching the required "unauthorized account action" / "honest-node divergence" impact bar.

### Likelihood Explanation
Triggering this only requires the original L1 message sender to call the standard L1 cancellation-start entry point for their own message at a time that the sequencer has already committed/consumed the corresponding L1-handler transaction, or for scraped events to be delivered/processed out of their L1 emission order. No privileged node or operator action is required — this is reachable purely through the message sender's own L1 transaction sequencing.

### Recommendation
Guard `mark_cancellation_request` (and `request_cancellation`) against all terminal states, not just `Committed`:
```rust
if self.is_committed() || self.is_consumed() || self.is_cancelled() {
    warn!(...);
} else {
    self.state = TransactionState::CancellationStartedOnL2;
}
```
Additionally, make `is_committed()`/`is_consumed()` rely on the immutable metadata flags (`self.committed`, a similarly-added `self.consumed`) rather than the single mutable `state` field, so a later transition can never erase evidence of an earlier terminal state.

### Proof of Concept
1. Add and commit an L1 handler tx `T` (`add_tx` → `commit_txs([T])`), so `T.state == Committed`, `T.committed == true`.
2. Deliver `Event::TransactionConsumed { tx_hash: T }` → `mark_consumed` sets `T.state = Consumed` (state overwritten, `T.committed` metadata untouched).
3. Deliver `Event::TransactionCancellationStarted { tx_hash: T, .. }` (as would occur from a late/legitimate `startL1ToL2MessageCancellation` call, or reordered scraper delivery) → `mark_cancellation_request` sees `is_committed() == false` (state is `Consumed`, not `Committed`) and sets `T.state = CancellationStartedOnL2`.
4. Call `validate_tx(T, unix_now)`: `update_time_based_state` returns early because `self.committed == true`, leaving state at `CancellationStartedOnL2`; `is_validatable()` now returns `true` for a transaction that was already committed and consumed on L1, whereas it should permanently report `AlreadyIncludedOnL2`/`ConsumedOnL1`.

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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L155-181)
```rust
    pub fn is_proposable(&self) -> bool {
        matches!(self.state, TransactionState::Pending)
    }

    pub fn is_committed(&self) -> bool {
        matches!(self.state, TransactionState::Committed)
    }

    /// Answers whether the transaction was fully cancelled on L2 (cancellation request timelock
    /// has expired).
    pub fn is_cancelled(&self) -> bool {
        matches!(self.state, TransactionState::CancelledOnL2)
    }

    pub fn is_consumed(&self) -> bool {
        matches!(self.state, TransactionState::Consumed)
    }

    /// Answers whether any node can include this transaction in a block. This is generally possible
    /// in all states in its lifecycle, except after it had already been added to block, or a short
    /// time after it's cancellation was requested on L1. In particular, this includes states
    /// like: a rejected transaction, a new timelocked transaction, a
    /// transaction whose cancellation was requested on L1 too recently (there will be a
    /// timelock for this).
    pub fn is_validatable(&self) -> bool {
        !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
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

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L144-169)
```rust
                Event::TransactionCancellationStarted {
                    tx_hash,
                    cancellation_request_timestamp,
                } => {
                    if !self.tx_manager.exists(tx_hash) {
                        warn!(
                            "Dropping cancellation request for old L1 handler transaction \
                             {tx_hash}: not in the provider and will never be scraped at this \
                             point."
                        );
                        continue;
                    }

                    self.tx_manager
                        .request_cancellation(tx_hash, cancellation_request_timestamp)
                        .inspect(|previous_request_timestamp| {
                            // Re-requesting a cancellation is meaningful for the L1 timelock, but
                            // for the l2 timelock we only consider the first cancellation
                            // relevant.
                            info!(
                                "Dropping duplicated cancellation request for {tx_hash} at \
                                 {cancellation_request_timestamp}, previous request block \
                                 timestamp still stands: {previous_request_timestamp}"
                            );
                        });
                }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L211-272)
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

    pub fn finalize_cancellation(&mut self, tx_hash: TransactionHash) {
        let Some(record) = self.records.get(&tx_hash) else {
            info!(
                "Attempted to finalize cancellation for non-existent transaction: {tx_hash}. This \
                 can happen if the transaction was too old to be scraped (e.g. it was created \
                 before we started scraping)."
            );
            return;
        };

        // Regardless of the state of the tx in the record, if we get the cancellation event from
        // the L1 contract, we delete this tx from the records and from the proposable index, even
        // if it was Pending and ready to be proposed (which is not supposed to happen, hence the
        // warning).
        if record.state != TransactionState::CancellationStartedOnL2 {
            warn!(
                "Attempted to finalize cancellation for transaction {tx_hash} that is not in the \
                 cancellation started on L2 state, but in the {:?} state.",
                record.state
            );
        }
        // This will also call maintain_indices to remove the tx from the proposable index.
        self.with_record(tx_hash, |r| r.mark_cancellation_finalized_on_l1());
        self.records.remove(&tx_hash);
    }

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
