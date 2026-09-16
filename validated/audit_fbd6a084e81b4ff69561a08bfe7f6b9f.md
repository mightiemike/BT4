### Title
Stale `consumed_queue` entry left behind when `finalize_cancellation` removes a `Consumed` transaction record - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager::finalize_cancellation` unconditionally removes a transaction's `TransactionRecord` from `self.records` without checking whether that transaction is currently referenced by `self.consumed_queue`. This mirrors the reported Visor Finance bug class: an entry is deleted from the primary store while a secondary index/mapping that points to it is left stale, breaking the documented invariant.

### Finding Description
The `TransactionManager` maintains two structures that must stay in sync:
- `records: Records` — the source of truth for every tracked L1 handler transaction.
- `consumed_queue: BTreeMap<BlockTimestamp, Vec<TransactionHash>>` — an index of transactions marked consumed, with a documented invariant: "A transaction is in the queue iff it is in the records and marked as consumed" [1](#0-0) .

A transaction is inserted into `consumed_queue` by `maintain_indices` whenever `record.get_consumed_at_timestamp()` is `Some`, which happens as soon as `mark_consumed` sets `consumed_at` [2](#0-1) . Crucially, `mark_consumed` sets `consumed_at` but this does **not** prevent the record's `state` field from later being overwritten: `mark_cancellation_request` only refuses to act if the record `is_committed()`, not if it is `Consumed` [3](#0-2) . So a transaction can be moved to `CancellationStartedOnL2` (and later, via `update_time_based_state`, to `CancelledOnL2`) even though it was already `Consumed` and is sitting in `consumed_queue`.

Once that state drifts away from `Consumed`, `finalize_cancellation` will remove the record from `self.records` entirely: it only *warns* if `record.state != CancellationStartedOnL2` but proceeds to call `self.records.remove(&tx_hash)` regardless of state [4](#0-3) . Unlike `clear_old_tx_from_consumed_queue`, which is the only code path that also prunes `consumed_queue` in lockstep with `records.remove` [5](#0-4) , `finalize_cancellation` never touches `consumed_queue`. The tx_hash therefore remains a dangling entry in `consumed_queue`, violating the stated invariant — directly analogous to `transferERC721` failing to `delete timelockERC721s[token]` after removing the token from the locked-tokens array.

### Impact Explanation
Because the record was fully removed from `self.records`, a subsequent `add_tx` call for the same `tx_hash` (e.g. the L1 scraper re-observing/re-processing the same L1 event, or a state-sync catch-up re-adding the hash) will call `create_record_if_not_exist`, which succeeds and creates a brand-new `Pending` record with `consumed_at = None` [6](#0-5) . This fresh record has no memory that the L1 message was already consumed, so `is_validatable()` returns `true` and the transaction can be validated and proposed into a new L2 block via `get_txs`/`validate_tx` [7](#0-6) . This effectively allows an L1→L2 message that was already consumed to be re-included and re-executed by the sequencer, i.e. duplicate execution of an L1 handler transaction — a concrete double-execution / fund-duplication risk, and an honest-node divergence risk if peers process the stale/duplicate record differently.

### Likelihood Explanation
The trigger requires a specific ordering of legitimate, attacker/L1-message-sender-reachable events: (1) the message is consumed on L1, (2) a cancellation request is issued for the same message hash (an L1 message sender can call the corresponding L1 cancellation entry point for messages they sent) before `clear_old_tx_from_consumed_queue`'s timelock evicts it, (3) the cancellation timelock elapses and a `validate_tx` call transitions the state to `CancelledOnL2`, and (4) `finalize_cancellation` fires. This is a race/ordering condition rather than a single-call exploit, and depends on how permissive the L1 Core contract itself is about starting a cancellation for an already-consumed message (not verifiable from this repository). Given this dependency, likelihood is assessed as low-to-moderate, but the internal state machine in this repo does not defensively guard against it.

### Recommendation
- In `finalize_cancellation`, before removing the record, check if `record.get_consumed_at_timestamp().is_some()` (or `record.state == Consumed`/was ever consumed) and refuse/no-op the cancellation-finalization for already-consumed transactions, since a consumed message can never legitimately be cancelled.
- Defensively, also guard `mark_cancellation_request` to reject cancellation requests on transactions that are already `Consumed`, not just `Committed`.
- Alternatively, when removing a record from `self.records`, always also purge any matching entry from `consumed_queue` (search-and-remove by `consumed_at` timestamp bucket), keeping the two structures atomically consistent, mirroring how `clear_old_tx_from_consumed_queue` already does both removals together.

### Proof of Concept
1. `add_tx` scrapes L1 handler transaction `T` (state: `Pending`).
2. `consume_tx(T, consumed_at, now)` is called → `mark_consumed` sets `consumed_at = Some(t0)`, state becomes `Consumed`, and `maintain_indices` inserts `T` into `consumed_queue[t0]` [2](#0-1) .
3. Before the consumption timelock cutoff is reached, `request_cancellation(T, block_timestamp)` is invoked (from an observed L1 cancellation-start event) → `mark_cancellation_request` does not check `is_consumed()`, sets state to `CancellationStartedOnL2` [3](#0-2) .
4. After the cancellation timelock elapses, any `validate_tx(T, ...)` call runs `update_time_based_state`, transitioning state to `CancelledOnL2` [8](#0-7) .
5. `finalize_cancellation(T)` is called (from an observed L1 cancellation-finalized event) → logs a warning (state isn't `CancellationStartedOnL2`) but proceeds to `self.records.remove(&T)` [4](#0-3) . `consumed_queue[t0]` still contains `T`.
6. `add_tx(T, ...)` is invoked again (e.g., re-scrape) → `create_record_if_not_exist` succeeds, creating a new `Pending` record for `T` with `consumed_at = None`.
7. `T` is now `is_validatable() == true` and can be returned by `get_txs`/validated by `validate_tx`, allowing the already-consumed L1 message to be proposed and executed again on L2.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L49-53)
```rust
    /// All consumed transactions that are waiting to be removed from the transaction manager.
    /// Invariant: Ordered lexicographically by the block timestamp where they were marked as
    /// consumed, then order-of-arrival for identical timestamps.
    /// Invariant 2: A transaction is in the queue iff it is in the records and marked as consumed.
    consumed_queue: BTreeMap<BlockTimestamp, Vec<TransactionHash>>,
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L72-145)
```rust
    pub fn get_txs(&mut self, n_txs: usize, now: u64) -> Vec<L1HandlerTransaction> {
        // Oldest        Now.sub(timelock)     Newest       Now
        //  |<---  passed  --->|                 |           |
        //  |<--- cooldown --->|                 |           |
        // t-------------------------------------------------->
        let cutoff = now.saturating_sub(self.config.l1_handler_proposal_cooldown_seconds.as_secs());
        let past_cooldown_txs = self.proposable_index.range(..cutoff);

        // Linear scan, but we expect this to be a small number of transactions (< 10 roughly).
        let unstaged_tx_hashes: Vec<_> = past_cooldown_txs
            .flat_map(|(_timestamp, tx_hashes)| tx_hashes.iter())
            .skip_while(|&&tx_hash| self.is_staged(tx_hash))
            .take(n_txs)
            .copied()
            .collect();

        for &tx_hash in unstaged_tx_hashes.iter() {
            let record = self.records.get(&tx_hash).expect("transaction should exist");
            assert_eq!(
                record.state,
                TransactionState::Pending,
                "Transaction {tx_hash} has state {:?}. Only Pending transactions should be in the \
                 proposable index.",
                record.state
            );
        }

        let mut txs = Vec::with_capacity(n_txs);
        let current_staging_epoch = self.current_staging_epoch; // borrow-checker constraint.
        for tx_hash in unstaged_tx_hashes {
            let newly_staged =
                self.with_record(tx_hash, |record| record.try_mark_staged(current_staging_epoch));
            assert_eq!(
                newly_staged,
                Some(true),
                "Inconsistent storage state: indexed l1 handler {tx_hash} is not in storage or \
                 wasn't marked as staged."
            );

            txs.push(self.records[&tx_hash].get_unchecked().clone());
        }
        txs
    }

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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L274-284)
```rust
    pub fn clear_old_tx_from_consumed_queue(&mut self, unix_now: u64) {
        let cutoff =
            unix_now.saturating_sub(self.config.l1_handler_consumption_timelock_seconds.as_secs());
        let still_timelocked = self.consumed_queue.split_off(&BlockTimestamp(cutoff));
        let passed_timelock = std::mem::replace(&mut self.consumed_queue, still_timelocked);
        for tx_hashes in passed_timelock.values() {
            for tx_hash in tx_hashes {
                self.records.remove(tx_hash);
            }
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L425-428)
```rust
            // If this tx was consumed, add it to the consumed queue.
            if let Some(consumed_at) = record.get_consumed_at_timestamp() {
                self.consumed_queue.entry(consumed_at).or_default().push(tx_hash);
            }
```

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
