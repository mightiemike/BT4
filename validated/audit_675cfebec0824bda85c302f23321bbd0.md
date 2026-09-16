### Title
Unbounded growth of the L1 handler `TransactionManager` records / `proposable_index` via cheap repeated L1→L2 messages - (File: crates/apollo_l1_events/src/transaction_manager.rs)

### Summary
The `TransactionManager` that tracks L1 handler transactions has no capacity bound, unlike the L2 mempool which enforces `capacity_in_bytes`. Every scraped L1→L2 message becomes a permanent `TransactionRecord` in `self.records` and, once its cooldown elapses, an entry in `self.proposable_index`, which is scanned linearly on every `get_txs()` call. Records are removed only via consumption-on-L1 (lazy, timelocked) or cancellation-on-L1 (also timelocked, and requires a second L1 call to finalize). An L1 message sender can therefore keep pushing many cheap `L1HandlerTransaction`s that are never consumed or cancelled, unboundedly growing these structures, degrading a per-block sequencer operation.

### Finding Description
`TransactionManager::add_tx` unconditionally calls `create_record_if_not_exist` and inserts a new `TransactionRecord` for every distinct L1 handler transaction hash scraped from L1, with no cap on the total number of outstanding records: [1](#0-0) 

Once a record becomes `Pending` and scraped, `maintain_indices` pushes its hash into `proposable_index`, keyed by scrape timestamp: [2](#0-1) 

`get_txs`, which the batcher/proposer calls every block to pull proposable L1 handler transactions, performs a linear scan over `proposable_index` and explicitly documents the assumption that this collection is small: [3](#0-2) 

Records are removed only in two ways, both lazy and gated by timelocks the message sender does not have to trigger:
- `finalize_cancellation`, which requires the L1 message sender to first `request_cancellation`, wait `l1_handler_cancellation_timelock_seconds` (default 5 minutes), and then submit a second L1 transaction to finalize the cancellation: [4](#0-3) 
- `consume_tx` / `clear_old_tx_from_consumed_queue`, which requires the message to actually be consumed on L1 and then waits `l1_handler_consumption_timelock_seconds` before removal: [5](#0-4) 

If the sender simply never consumes and never finalizes cancellation, the record and its `proposable_index`/`consumed_queue` entries persist indefinitely. Nothing in `TransactionManagerConfig` or `L1EventsProviderConfig` bounds the total number of records or proposable entries: [6](#0-5) 

This mirrors the reported Opyn bug class: an unprivileged actor (there, `depositUSDC`/`withdrawUSDC`; here, an L1 message sender issuing many cheap `sendMessageToL2` calls) repeatedly performs cheap operations that push into an array/index with no bound, and that array/index is subsequently scanned in a hot path (`depositAuction`/`withdrawAuction` there; `get_txs` here) whose cost was assumed to stay small.

### Impact Explanation
`get_txs` is invoked on every block-building attempt to select proposable L1 handler transactions. As `proposable_index` grows well beyond the "< 10 roughly" assumption baked into the code comment, the per-block linear scan, cloning, and staging logic in `get_txs`/`with_record`/`maintain_indices` becomes increasingly expensive. Because `records` never shrinks unless the attacker chooses to consume/cancel their own spam, this is an attacker-controlled, monotonically growing cost on the sequencer's block-building critical path — a form of unbounded resource growth reachable purely from L1 (an in-scope actor per the rules) that degrades a core sequencer function and can slow or disrupt block production.

### Likelihood Explanation
Sending an L1→L2 message only requires calling the Starknet core contract's `sendMessageToL2` and paying the (small, fixed) L1 message fee; there is no minimum "useful" payload size or requirement to ever consume/cancel it. Any L1 account can trivially and cheaply repeat this call thousands of times, so the likelihood of triggering unbounded growth of `records`/`proposable_index` is high and requires no special privileges.

### Recommendation
- Bound the total number of outstanding (uncommitted/unconsumed/uncancelled) `TransactionRecord`s the `TransactionManager` will track, analogous to the mempool's `capacity_in_bytes`, rejecting or deferring further scraped messages once the bound is reached.
- Avoid an unconditional linear scan over `proposable_index` sized by attacker-controlled input in `get_txs`; enforce/verify the "small number of transactions" assumption with an explicit cap and a defined eviction/rejection policy when exceeded.
- Consider requiring a minimum L1 fee/deposit per L1 handler message proportional to how long it may sit unconsumed, so spamming many pending records is not cost-free relative to the sequencer-side storage/scan burden it imposes.

### Proof of Concept
1. From L1, repeatedly call the Starknet core contract's message-sending entry point to generate many distinct `L1HandlerTransaction`s targeting L2, each with a trivial/no-op payload, without ever triggering consumption or requesting cancellation.
2. Each such message is scraped and added via `TransactionManager::add_tx` (crates/apollo_l1_events/src/transaction_manager.rs:173-209), creating a permanent `TransactionRecord`.
3. Once each record's cooldown (`l1_handler_proposal_cooldown_seconds`) elapses, it is added to `proposable_index` (transaction_manager.rs:376-408) and remains there/in `records` indefinitely, since the attacker never calls the L1 consume or cancel-finalize flows.
4. Repeating this at scale grows `records` and `proposable_index` without bound, increasing the cost of every subsequent `get_txs` call (transaction_manager.rs:72-114), which the batcher invokes on every block-building cycle.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L72-86)
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L211-245)
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
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L247-284)
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L376-408)
```rust
    // Update `proposable_index` and `consumed_queue` indices with this transaction.
    fn maintain_indices(&mut self, hash: TransactionHash) {
        if let Some(record) = self.records.get(&hash) {
            let TransactionPayload::Full { scrape_timestamp, .. } = record.tx else {
                // We haven't scraped this tx yet, so it isn't indexed.
                return;
            };

            let tx_hash = hash;
            // Check if we need to add this tx to the proposable index.
            if record.is_proposable() {
                // Assumption: txs will only be added to the index once, on arrival, so this
                // preserves arrival order.
                let tx_hashes = self.proposable_index.entry(scrape_timestamp).or_default();
                if !tx_hashes.contains(&tx_hash) {
                    tx_hashes.push(tx_hash);
                }
            } else {
                // This tx needs to be removed from the proposable index if it was on it.
                // Remove from the vec for this timestamp, and drop the entry if it becomes empty.
                match self.proposable_index.entry(scrape_timestamp) {
                    Entry::Occupied(mut entry) => {
                        let tx_hashes = entry.get_mut();
                        if let Some(index_in_vec) = tx_hashes.iter().position(|&h| h == tx_hash) {
                            tx_hashes.remove(index_in_vec);
                            if tx_hashes.is_empty() {
                                entry.remove();
                            }
                        }
                    }
                    Entry::Vacant(_) => {}
                }
            }
```

**File:** crates/apollo_l1_events_config/src/config.rs (L79-93)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TransactionManagerConfig {
    // How long to wait before allowing new L1 handler transactions to be proposed (validation is
    // available immediately), from the moment they are scraped.
    pub l1_handler_proposal_cooldown_seconds: Duration,
    /// How long to allow a transaction requested for cancellation to be validated against
    /// (proposals are banned upon receiving a cancellation request).
    pub l1_handler_cancellation_timelock_seconds: Duration,
    /// How long to wait before allowing a transaction that was consumed on L1 to be removed from
    /// the transaction managers records.
    // The motivation behind this timelock is to make debugging easier and to be more careful
    // about permanently deleting information.
    // This only delays a cleanup action, so the duration of the timelock wouldn't affect the UX.
    pub l1_handler_consumption_timelock_seconds: Duration,
}
```
