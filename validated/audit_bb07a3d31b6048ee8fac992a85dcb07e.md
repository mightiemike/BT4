### Title
Unbounded per-mutation full scan of the L1-handler proposable index enables an L1-message-sender DoS of the L1 Events Provider - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager` keeps every scraped, not-yet-consumed/cancelled L1→L2 message in `records` and indexes proposable ones in `proposable_index`. On *every* state-mutating call (`add_tx`, `validate_tx`, `commit_txs`, `request_cancellation`, `finalize_cancellation`, `consume_tx`) the helper `with_record` invokes `maintain_indices`, which unconditionally recomputes `oldest_pending_l1_block_timestamp()` by scanning the **entire** `proposable_index` (`self.proposable_index.values().flatten()...min()`). `get_txs` similarly performs a linear scan/`skip_while` over all past-cooldown entries. Neither structure has an upper bound on size — it only shrinks when a message is consumed or cancelled on L1, both of which are outside the sequencer's control and depend on L1 confirmation. This mirrors the reported bug class ("functions become unexecutable/unbounded gas due to a huge array") except the resource exhausted here is sequencer CPU time per L1 event/tx rather than EVM gas.

### Finding Description
`maintain_indices` is the single choke point through which every record mutation passes: [1](#0-0) 
It calls `oldest_pending_l1_block_timestamp`, an O(n) full scan over `proposable_index` for every mutation: [2](#0-1) 
`get_txs`, used on every block-proposal attempt, also performs an unbounded linear scan/`skip_while` over the "past cooldown" prefix of `proposable_index`, explicitly documented as an assumption that the list stays small ("Linear scan, but we expect this to be a small number of transactions (< 10 roughly)"): [3](#0-2) 
`proposable_index`/`records` only shrink via `finalize_cancellation` or `consume_tx`, both driven by asynchronous L1 confirmation events, not by sequencer-controlled logic: [4](#0-3) [5](#0-4) 
Every new L1→L2 message reaching the sequencer (via `add_tx`, which is invoked for every scraped `LogMessageToL2` L1 event) is added to the index and never bounded in count: [6](#0-5) 
There is no configured maximum on `records`/`proposable_index` size anywhere in `TransactionManagerConfig` usage in this file — only a per-block *proposal* cap (`max_l1_handler_txs_per_block`) limits how many are pulled into a block at once, not how many can accumulate as pending.

Because block-building throughput is capped by `max_l1_handler_txs_per_block`/bouncer limits while L1 message submission is only rate-limited by L1 gas price (an attacker can cheaply submit many low-cost `sendMessageToL2` calls), an attacker can grow the backlog of pending, not-yet-consumed L1 handler transactions faster than the sequencer can drain it. Since every one of these pending transactions repeatedly triggers a full-index scan on nearly every subsequent mutation (each new message, each `validate_tx`/`get_txs` cycle across proposal/validation rounds), the per-event cost grows linearly with backlog size, degrading into effectively quadratic total work as the backlog grows across many blocks.

### Impact Explanation
This is reachable purely by an unprivileged L1 account sending L1→L2 messages (an "L1 message sender", explicitly in-scope). If the backlog grows large enough, the L1 Events Provider component's per-event/per-block processing latency increases without bound, which can delay or stall `get_txs`/`validate_tx` responses used during block proposal and validation. Sustained backlog growth can degrade block-production latency network-wide (all validators run the same code and are exposed to the same growing index once L1 messages are scraped), risking missed block deadlines — a liveness/availability impact (network unable to confirm new transactions in a timely manner), consistent with Medium/High severity resource-exhaustion findings.

### Likelihood Explanation
Likelihood is moderate to high: the only cost to the attacker is L1 gas for submitting many cheap `sendMessageToL2` calls (no L2 fee is paid until/unless the message executes), while the sequencer must scrape, store, and repeatedly re-scan every one of them until it is naturally consumed or cancelled via L1 confirmation, which the attacker fully controls by simply not letting the induced L1 handler executions complete/consume (e.g., targeting a contract or entry point designed to make execution revert or be rejected, or simply outproducing the sequencer's consumption rate).

### Recommendation
- Cap `records`/`proposable_index` growth (e.g., a configurable maximum number of pending/tracked L1 handler transactions, with backpressure or rejection once exceeded) similar to the mempool's capacity-based eviction (`try_make_space` in `apollo_mempool`).
- Avoid recomputing `oldest_pending_l1_block_timestamp` via a full scan on every mutation; maintain it incrementally (e.g., a min-heap or track the minimum directly in `proposable_index`'s ordering) so the metric update is O(log n) or O(1) instead of O(n).
- Bound/paginate the `skip_while` scan in `get_txs` so its cost cannot exceed a fixed budget regardless of backlog size, and add an explicit metric/alert (beyond the existing `L1_MESSAGE_PROVIDER_NUM_PENDING_TXS`) that fails fast or throttles scraping when the backlog crosses a safety threshold.

### Proof of Concept
1. Attacker repeatedly calls the L1 core contract's `sendMessageToL2` with minimal calldata/gas, targeting an L2 entry point/contract chosen so the resulting L1 handler transaction is either never proposed promptly (bounded by `max_l1_handler_txs_per_block`) or, once executed, does not lead to a corresponding "consumed" L1 event being observed quickly (e.g., by using an L2 target that reverts, keeping the record perpetually `Pending`/re-added rather than `Consumed`).
2. Each such message causes `add_tx` → `maintain_indices` → `oldest_pending_l1_block_timestamp` to run, each call being O(current index size). [7](#0-6) 
3. As thousands of such records accumulate (limited only by attacker's L1 gas budget), every subsequent `add_tx`/`validate_tx`/`get_txs` call across all future blocks becomes measurably slower, since each performs a scan proportional to backlog size.
4. Over many blocks, cumulative CPU spent in `TransactionManager` scans grows superlinearly, increasing latency of L1 handler tx availability and risking block-production deadline misses for every validator running the same code.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L72-97)
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L341-349)
```rust
    fn with_record<F, R>(&mut self, hash: TransactionHash, f: F) -> Option<R>
    where
        F: FnOnce(&mut TransactionRecord) -> R,
    {
        let record = self.records.get_mut_unchecked(hash)?;
        let result = f(record);
        self.maintain_indices(hash);
        Some(result)
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L365-374)
```rust
    /// The L1 block timestamp of the oldest pending (proposable, uncommitted) L1 handler tx, or
    /// `None` when none are pending. Scans the proposable index (expected to hold few transactions)
    /// for the minimal L1 emission timestamp, rather than relying on scrape order.
    fn oldest_pending_l1_block_timestamp(&self) -> Option<BlockTimestamp> {
        self.proposable_index
            .values()
            .flatten()
            .filter_map(|tx_hash| self.records.get(tx_hash)?.tx.created_at_block_timestamp())
            .min()
    }
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L376-422)
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

            let num_pending_txs = self.proposable_index.len();
            L1_MESSAGE_PROVIDER_NUM_PENDING_TXS.set_lossy(num_pending_txs);

            // Export the oldest pending tx's L1 block timestamp so an alert can fire when a single
            // L1 handler waits on L1 for too long without being committed to L2. We export the
            // timestamp (not the age) so the alert can compute `time() - timestamp` and have the
            // age grow correctly even while the provider is idle. 0 means nothing is
            // pending.
            let oldest_pending_tx_timestamp = self
                .oldest_pending_l1_block_timestamp()
                .map(|timestamp| timestamp.0)
                .unwrap_or_default();
            L1_MESSAGE_PROVIDER_OLDEST_PENDING_TX_L1_TIMESTAMP_SECONDS
```
