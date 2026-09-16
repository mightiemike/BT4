### Title
Cancellation request on an already-Consumed L1 handler transaction overwrites its terminal state, re-enabling proposal/validation of an already-executed L1→L2 message - (File: crates/apollo_l1_events/src/transaction_record.rs)

### Summary
`TransactionRecord::mark_cancellation_request` unconditionally overwrites `self.state` to `CancellationStartedOnL2` for any transaction that is not `committed`, without checking whether the transaction is already in the terminal `Consumed` (or `Rejected`) state. This mirrors the analog bug class in the report: a state machine that checks/transitions state in the wrong order/precedence, silently discarding a terminal state and reaching an invalid, unintended state that changes downstream eligibility logic (`is_validatable`, `is_proposable`, `is_consumed`).

### Finding Description
`TransactionRecord` models the lifecycle of an L1→L2 (`L1Handler`) message with an explicit state enum `TransactionState` (`Pending`, `Committed`, `Rejected`, `CancellationStartedOnL2`, `CancelledOnL2`, `CancellationFinalizedOnL1`, `Consumed`) [1](#0-0) .

`mark_cancellation_request` is invoked by `TransactionManager::request_cancellation`, which is called directly from L1 event ingestion whenever the L1 emits `MessageToL2CancellationStarted` — an event that any L1 sender of the original message can trigger by calling `startL1ToL2MessageCancellation` on the Starknet core contract [2](#0-1) [3](#0-2) .

The guard in `mark_cancellation_request` only checks `is_committed()` (the `committed` boolean flag), not whether the transaction has already reached the `Consumed` state: [4](#0-3) 

If a `TransactionConsumed` L1 event has already been processed (`mark_consumed`, setting `state = Consumed`) but the transaction's `committed` flag is not necessarily what gates future cancellation handling, a subsequently-processed (or racing, out-of-order relative to arrival) `TransactionCancellationStarted` event for the same hash will overwrite `state` from `Consumed` to `CancellationStartedOnL2`: [5](#0-4) 

This is analogous to the reported bug class: the code recognizes only `committed` as blocking further transitions, while other equally-terminal states (`Consumed`, and by extension `Rejected`) are not excluded, so an "expired"/terminal-equivalent state can be silently clobbered by a state transition meant only for still-live transactions — exactly the "missing/omitted terminal-state guard causing wrong state transition" pattern in the referenced report (Late/Expired state improperly overwritten due to incomplete transition guards).

The downstream eligibility predicates only look at `self.state`: [6](#0-5) 

Once `state` becomes `CancellationStartedOnL2` (which is neither `Committed`, `Cancelled`, nor `Consumed`), `is_validatable()` returns `true` again for a transaction whose message has already been consumed by the core contract. `TransactionManager::validate_tx` only special-cases `Committed`/`CancelledOnL2`/`Consumed` as invalid; `CancellationStartedOnL2` falls through to being treated as validatable/proposable, and it will re-enter the `proposable_index` via `maintain_indices` since `is_proposable()` only checks for `Pending` — so this particular window does not directly make it proposable, but it does make it re-validatable in `validate_tx`, and more importantly it corrupts the terminal accounting used by `is_consumed()`/`get_consumed_at_timestamp()` bookkeeping paths that assume `Consumed` is sticky (e.g., `consume_tx`'s double-consumption detection and `maintain_indices`'s consumed-queue tracking rely on `state == Consumed` remaining true until removal).

### Impact Explanation
If an L1 sender races a cancellation-start event against (or issues one shortly after) message consumption, the transaction manager's internal state for that L1 handler transaction is corrupted: it flips from the terminal `Consumed` marker back to `CancellationStartedOnL2`. This breaks the invariants documented in the code itself (`is_validatable` comment: "generally possible in all states ... except after it had already been added to block") and can defeat the double-consumption panic-guard in `consume_tx`, since a later legitimate re-check of `is_consumed()` would incorrectly return `false`. This can result in state-machine divergence between sequencer instances relative to L1 event processing timing/ordering, and inconsistent validation results (`InvalidValidationStatus::ConsumedOnL1` vs `ValidationStatus::Validated`) for the same L1 handler transaction depending on event arrival order — a correctness/consensus-adjacent bug in a component reachable purely by an L1 message sender's on-chain actions (no privileged operator/proposer role required).

### Likelihood Explanation
Reaching this requires only that the *original sender* of the L1→L2 message (an unprivileged L1 account) calls `startL1ToL2MessageCancellation` on the core contract after (or concurrent with) the point where the corresponding message has already been consumed on L2 but the consumption event is processed with different relative ordering/timing versus the cancellation-start event by different nodes' scrapers. Because L1 event scraping/ordering across nodes is not synchronized to a single global order relative to the mutation on `TransactionRecord.state`, this is a plausible, not merely theoretical, sequencing race.

### Recommendation
Guard `mark_cancellation_request` (and any other state-setting transition) against all terminal states, not just `committed`:
```rust
if self.is_committed() || self.is_consumed() || matches!(self.state, TransactionState::Rejected) {
    warn!(...)
} else {
    self.state = TransactionState::CancellationStartedOnL2;
}
```
More generally, replace ad-hoc field-based guards (`committed`, `rejected`, booleans) with an explicit state-transition table that enumerates the valid `(current_state, event) -> next_state` pairs, so it is impossible to overwrite terminal states (`Committed`, `Consumed`, `Rejected`, `CancelledOnL2`, `CancellationFinalizedOnL1`) from a later, order-dependent event.

### Proof of Concept
Conceptual sequence (state machine walk, not an on-chain PoC since this is sequencer-internal logic):
1. L1 sender submits an L1→L2 message; scraper emits `Event::L1HandlerTransaction`, `TransactionManager::add_tx` creates a `TransactionRecord` with `state = Pending`.
2. The corresponding `L1HandlerTransaction` gets executed/committed on L2 and later consumed on L1; scraper emits `Event::TransactionConsumed`; `TransactionManager::consume_tx` → `TransactionRecord::mark_consumed` sets `state = Consumed`.
3. The same L1 sender (who is fully entitled to call `startL1ToL2MessageCancellation` — no special privilege needed) had already broadcast (or later broadcasts, with an out-of-order scrape/catch-up) a cancellation request for the same message; scraper emits `Event::TransactionCancellationStarted`; `L1EventsProvider::add_events` calls `TransactionManager::request_cancellation` → `TransactionRecord::mark_cancellation_request`.
4. Because the guard only checks `self.is_committed()` (false, since `committed` flag was never set for this record) `state` is overwritten to `CancellationStartedOnL2`, discarding the `Consumed` terminal marker.
5. A subsequent call to `validate_tx` for this `tx_hash` no longer hits the `TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1` branch [7](#0-6) , and `is_validatable()` returns `true`, contradicting the actual on-L1 consumed status of the message.

### Citations

**File:** crates/apollo_l1_events/src/transaction_record.rs (L77-101)
```rust
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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L116-141)
```rust
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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L269-279)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub enum TransactionState {
    CancellationStartedOnL2,
    CancellationFinalizedOnL1,
    CancelledOnL2,
    Committed,
    Consumed,
    #[default]
    Pending,
    Rejected,
}
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L126-136)
```rust
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
