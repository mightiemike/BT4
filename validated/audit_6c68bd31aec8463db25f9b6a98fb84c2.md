### Title
Local L1-handler cancellation timelock permanently orphans deposits before L1 cancellation finalizes, freezing L1→L2 messages - (File: crates/apollo_l1_events/src/transaction_record.rs)

### Summary
The `TransactionManager`/`TransactionRecord` state machine that tracks Starknet L1→L2 ("L1 handler") messages transitions a message from `CancellationStartedOnL2` to a permanent `CancelledOnL2` state purely based on a locally-configured `l1_handler_cancellation_timelock_seconds` timer, independent of whether the cancellation is ever actually finalized on L1. Once this local timer elapses the sequencer refuses forever to validate or propose the transaction, even if the sender never calls the L1 contract's finalize-cancellation function (or the finalize call reverts/never happens). This mirrors the `WardenPledge` pause bug class: an action that "takes effect immediately" (here: the mere passage of a locally-tracked timer following a cancellation *request*) unilaterally and permanently blocks the legitimate completion of a pending operation (consuming/executing the L1 message on L2) that the depositor still had every right to complete, with no way to reverse it once triggered.

### Finding Description
Starknet's L1→L2 messaging design requires a sender to first call `startL1ToL2MessageCancellation` on L1, wait out a delay enforced by the L1 contract itself, and only then call the L1 finalize function to actually cancel the message and reclaim the L1 fee. Until finalization actually occurs on L1, the message is still logically "pending" and should remain consumable/executable on L2.

The sequencer implementation does not wait for L1 finalization to block the message. Instead, as soon as a `MessageToL2CancellationStarted` event is scraped, `TransactionManager::request_cancellation` → `TransactionRecord::mark_cancellation_request` immediately moves the record to `CancellationStartedOnL2` and bans it from being proposed [1](#0-0) [2](#0-1) . Then, purely based on the passage of `l1_handler_cancellation_timelock_seconds` (a local L2 config value, e.g. 300 seconds by default) since the *request* — not since any L1-confirmed finalize event — `update_time_based_state` unconditionally transitions the record to `CancelledOnL2`: [3](#0-2) .

Once in `CancelledOnL2`, `is_validatable()` returns `false` permanently [4](#0-3) , and `validate_tx`/`get_txs` in the `TransactionManager` will never again validate or propose it [5](#0-4) . There is no code path that reverses `CancelledOnL2` back to `Pending` if the sender does not (or cannot) finalize the cancellation on L1 — the only other transition out of the cancellation lineage is `mark_cancellation_finalized_on_l1`, which is driven by an actual L1 `MessageToL2Canceled` event and removes the record entirely [6](#0-5) . If that L1 finalize event never arrives (e.g., the sender changes their mind, the finalize call reverts, or the L1-side cancellation delay — which can be substantially longer than the local `l1_handler_cancellation_timelock_seconds` value — has not yet elapsed on L1), the message is stuck: it cannot be executed on L2 (blocked by `CancelledOnL2`) and it is not actually released/refunded on L1 either (finalize never happened). The deposit/message is permanently orphaned.

The test suite explicitly documents and locks in this exact behavior — the cancellation timelock "prevents proposal forever" as soon as a cancellation request lands, well before the transaction is genuinely finalized as cancelled: [7](#0-6) , and validation permanently returns `CancelledOnL2` after the local timelock, confirmed idempotently: [8](#0-7) .

### Impact Explanation
This causes a **permanent freezing of state/funds represented by the L1→L2 message**: any user who sends an L1 message intending to trigger an L2 action (e.g., a token deposit/mint), and who (or whose L1 counterpart) issues a cancellation request that is not (or cannot yet be) finalized on L1 within the local, sequencer-configured `l1_handler_cancellation_timelock_seconds`, will find their message permanently unexecutable on L2 while it also remains un-refunded on L1. Because the L2-side ban is irreversible and independent of the true L1 finalization status, this is not a temporary inconvenience — it is a state the record can never recover from, matching the "permanent freezing of funds" acceptance criterion, and is reachable simply by an ordinary L1 message sender interacting with the L1 messaging contract as designed.

### Likelihood Explanation
The trigger requires no privileged access — any L1 message sender who requests a cancellation (a standard, permissionless part of the Starknet L1↔L2 messaging protocol) is affected once the local timelock elapses, regardless of whether they ever complete/intend to complete the L1-side finalization. Given that `l1_handler_cancellation_timelock_seconds` is a locally configured value (300s in the shipped config) decoupled from the L1 contract's actual cancellation delay, the mismatch window is realistically and routinely reachable, not merely theoretical.

### Recommendation
Do not permanently transition a record to `CancelledOnL2` based solely on the local timelock elapsing since the *cancellation request*. Instead, only treat a message as truly cancelled once an actual `MessageToL2Canceled` (finalize) event is observed from L1, or otherwise verify against the L1 contract's actual cancellation-delay/state before banning the transaction irreversibly. If a "soft" temporary ban is desired to protect against transiently proposing a tx that is likely to be cancelled, ensure the local timelock is a superset (never shorter) of the real L1 cancellation delay, and add a mechanism to revert to `Pending` if the request is not finalized within a bounded, safe window.

### Proof of Concept
Referenced from the existing test suite, which encodes and asserts the exact bug behavior (proposal is permanently banned as soon as a cancellation request timelock elapses, without any L1 finalize event ever having been observed): [9](#0-8) [10](#0-9) 

These tests show: (1) a cancellation request is scraped; (2) proposal of the tx is immediately banned; (3) after only the local `cancellation_timelock` (independent of any L1 finalize event), `validate_tx` starts returning `Invalid(CancelledOnL2)` permanently, including idempotently on repeated calls — with no code path ever restoring the transaction to `Pending`.

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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L163-181)
```rust
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L221-245)
```rust
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
```

**File:** crates/apollo_l1_events/tests/timing_flows.rs (L39-139)
```rust
fn l1_handler(index: u64) -> ExecutableL1HandlerTransaction {
    match message_to_l2(index) {
        Event::L1HandlerTransaction { l1_handler_tx, .. } => l1_handler_tx,
        _ => unreachable!(),
    }
}

#[tokio::test]
async fn timing_flows() {
    let time_starts_at = 1;
    let clock = Arc::new(FakeClock::new(time_starts_at));

    let cancellation_timelock = 2;
    let new_message_cooldown = 1;
    let consumption_timelock = 1;
    let l1_config = L1EventsProviderConfig {
        l1_handler_cancellation_timelock_seconds: Duration::from_secs(cancellation_timelock),
        l1_handler_consumption_timelock_seconds: Duration::from_secs(consumption_timelock),
        l1_handler_proposal_cooldown_seconds: Duration::from_secs(new_message_cooldown),
        ..Default::default()
    };
    let mut l1_events_provider = L1EventsProvider::new(
        l1_config,
        Arc::new(MockL1EventsProviderClient::default()),
        Arc::new(MockStateSyncClient::default()),
        Some(clock.clone()),
    );
    l1_events_provider
        .initialize(
            BlockNumber(time_starts_at),
            [
                cancellation_request(1, BlockTimestamp(1)), // Unknown, dropped silently.
                message_to_l2(2),
                message_to_l2(3),
                cancellation_request(2, BlockTimestamp(3)),
            ]
            .into(),
        )
        .await
        .unwrap();

    l1_events_provider
        .add_events(
            [message_to_l2(4), cancellation_request(3, BlockTimestamp(5)), message_to_l2(5)].into(),
        )
        .unwrap();

    // Ignored, an existing cancellation request on L2 is stronger than a new one.
    l1_events_provider.add_events([cancellation_request(3, BlockTimestamp(10))].into()).unwrap();

    l1_events_provider
        .commit_block([].into(), [].into(), l1_events_provider.current_height)
        .unwrap();

    l1_events_provider.start_block(l1_events_provider.current_height, Propose).unwrap();
    // Everything's timelocked.
    assert_eq!(l1_events_provider.get_txs(2, l1_events_provider.current_height).unwrap(), []);

    clock.advance(Duration::from_secs(3));
    assert_eq!(clock.unix_now(), 4);

    // Cancellation request is stronger than new message cooldown, and prevents proposal forever.
    assert_eq!(l1_events_provider.get_txs(2, l1_events_provider.current_height).unwrap(), []);

    // But validate still works, cause cancellation timelock hasn't passed yet for anyone.
    l1_events_provider.start_block(l1_events_provider.current_height, Validate).unwrap();
    assert_eq!(
        l1_events_provider.validate(tx_hash!(2), l1_events_provider.current_height).unwrap(),
        Validated
    );
    assert_eq!(
        l1_events_provider.validate(tx_hash!(3), l1_events_provider.current_height).unwrap(),
        Validated
    );
    assert_eq!(
        l1_events_provider.validate(tx_hash!(4), l1_events_provider.current_height).unwrap(),
        Validated
    );

    clock.advance(Duration::from_secs(2));
    assert_eq!(clock.unix_now(), 6);
    // Passed timelock for the first non-cancelled transaction.
    l1_events_provider.start_block(l1_events_provider.current_height, Propose).unwrap();
    assert_eq!(
        l1_events_provider.get_txs(2, l1_events_provider.current_height).unwrap(),
        vec![l1_handler(4)]
    );

    // One of the l1 handlers is passed its cancellation timelock, no longer validatable.
    l1_events_provider.start_block(l1_events_provider.current_height, Validate).unwrap();
    for _ in 0..2 {
        assert_eq!(
            l1_events_provider.validate(tx_hash!(2), l1_events_provider.current_height).unwrap(),
            Invalid(CancelledOnL2)
        ); // Check twice for idempotency.
    }
    assert_eq!(
        l1_events_provider.validate(tx_hash!(3), l1_events_provider.current_height).unwrap(),
        Validated
    );

```

**File:** crates/apollo_l1_events/src/l1_events_provider_tests.rs (L838-857)
```rust
#[test]
fn validate_tx_cancellation_requested_expired_returns_cancelled() {
    // Setup.
    let tx_1 = l1_handler(2);
    let mut l1_events_provider = L1EventsProviderContentBuilder::new()
        .with_nonzero_timelock_setup()
        .with_cancelled_txs([tx_1.clone()])
        .with_state(ProviderState::Validate)
        .build_into_l1_provider();

    // Test.
    // Should return Invalid(CancelledOnL2),
    let status =
        l1_events_provider.validate(tx_1.tx_hash, l1_events_provider.current_height).unwrap();
    assert_eq!(status, InvalidValidationStatus::CancelledOnL2.into());
    // Idempotent.
    let status2 =
        l1_events_provider.validate(tx_1.tx_hash, l1_events_provider.current_height).unwrap();
    assert_eq!(status2, InvalidValidationStatus::CancelledOnL2.into());
}
```
