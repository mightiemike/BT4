Based on my analysis of the codebase, I've identified a strong structural analog to the reported CVE pattern (state entries that are only partially cleared or held indefinitely without being tied to an attacker-affordable cap).

### Title
L1 handler transaction records in `TransactionManager` are retained in memory indefinitely after being Committed or Rejected, with no cap tied to L1 consumption timing - ([File: crates/apollo_l1_events/src/transaction_manager.rs])

### Summary
`TransactionManager::records` stores a full `TransactionRecord` (including payload) for every L1-to-L2 message ever scraped, keyed by transaction hash. Once a record reaches the `Committed` or `Rejected` state, nothing in the code path removes it from `records` — the only removal paths are `finalize_cancellation` (L1 cancellation) and `clear_old_tx_from_consumed_queue` (L1 consumption, itself gated by a lazy sweep that only fires on the next `consume_tx` call). This mirrors the CVE-2026-16028 pattern: an entity reaching a terminal/"closed" state has some of its fields cleared/updated, but the container entry itself is never purged from the parent table.

### Finding Description
`TransactionManager::commit_txs` marks transactions `Committed` or `Rejected` via `with_record`, which only updates the `proposable_index`/`consumed_queue` bookkeeping, and explicitly never removes anything from `self.records`: [1](#0-0) 

The struct's own documentation confirms records persist regardless of Committed/Rejected state and are removed only via two decoupled, L1-driven events: [2](#0-1) 

The only two removal call sites in the entire file are: [3](#0-2) [4](#0-3) 

Every new L1-to-L2 message inserts a fresh record via `add_tx`/`create_record_if_not_exist`: [5](#0-4) [6](#0-5) 

Crucially, the L2-side lifecycle (Pending → staged → Committed/Rejected) advances purely from sequencer-local block production, driven entirely by an L1 message sender submitting messages — this is fast (per L2 block). The only cleanup trigger (L1 consumption event) depends on the cadence of L1 state updates, which is much slower and is not bounded or throttled relative to the rate of L1 message submission. This is functionally identical to the reported bug class: `SETTINGS_MAX_CONCURRENT_STREAMS`/mempool-style caps bound "live" concurrent items, but growth here comes from items whose "concurrency" has already been released (committed/rejected) yet whose table entry is retained regardless.

### Impact Explanation
An unprivileged L1 message sender can flood `sendMessageToL2` to generate a large number of distinct L1 handler transaction hashes. Each is scraped, gets a `TransactionRecord` (holding the full `L1HandlerTransaction` payload — contract address, selector, calldata, timestamps), and is committed/rejected quickly on L2. None of these entries are freed until the corresponding L1 consumption event is scraped and its timelock (`l1_handler_consumption_timelock_seconds`) elapses. Because L1 state updates lag well behind L2 block production and L1 message submission, `records` can accumulate a large, unbounded-relative-to-caps backlog of full-payload entries, growing sequencer memory usage over time. Left unbounded, this can degrade or crash the L1 events provider component (used by both proposer and validator flows in every sequencer node), impacting the network's ability to process L1 handler transactions and, in the extreme, sequencer availability for block building.

### Likelihood Explanation
Sending L1-to-L2 messages is available to any L1 account and requires no special sequencer privileges. Unlike the p2p/stream analog, this does cost L1 gas per message, which somewhat limits the trivial "near-free" amplification seen in the CVE, but the amplification factor (small L1 cost vs. persistent large in-memory record retained on every honest node for an indeterminate, consumption-cadence-dependent period) is still asymmetric and directly reachable by an ordinary L1 sender without needing any L2 privileges.

### Recommendation
Bound the `records` table independently of L1 consumption/cancellation events — e.g., enforce a maximum table size or per-time-window cap with eviction/backpressure for already-terminal (`Committed`/`Rejected`) records, or apply an L2-side TTL/retention window (analogous to `committed_nonce_retention_block_count` in the mempool) so that terminal entries are pruned once they are no longer needed for staging/validation bookkeeping, independent of when (or whether) the L1 consumption event is observed.

### Proof of Concept
1. An attacker repeatedly calls `sendMessageToL2` on the L1 Starknet core contract with minimal calldata, generating N distinct L1-to-L2 messages.
2. The L1 events scraper picks these up; `TransactionManager::add_tx` creates a full `TransactionRecord` for each in `records`.
3. Each message is proposed and committed (or rejected) on L2 within a few blocks via `commit_txs`, transitioning the record's `state` to `Committed`/`Rejected` — but the entry remains in `records` per [7](#0-6) .
4. Because clearing requires an actual `ConsumedMessageToL2` event (tied to the much slower cadence of L1 state updates) plus the `l1_handler_consumption_timelock_seconds` delay, repeating step 1 faster than the L1 state-update/consumption cadence causes `records` to grow without bound relative to any concurrency cap, consuming increasing sequencer memory on every node running the L1 events provider.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L29-54)
```rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransactionManager {
    /// Storage of all l1 handler transactions --- keeps transactions until they can be safely
    /// removed, like when they are consumed on L1, or fully cancelled on L1.
    pub records: Records,
    pub config: TransactionManagerConfig,
    /// Ordered lexicographically by scraping moment timestamp, then order-of-arrival for
    /// identical timestamps, also at any point the staged transactions are a prefix of the
    /// structure under this order.
    /// Invariant: contains all hashes of transactions that are proposable, and only them.
    /// Invarariant 2: Once removed from this index, a transaction will never be proposed again.
    proposable_index: BTreeMap<UnixTimestamp, Vec<TransactionHash>>,
    /// Generation counter used to prevent double usage of an l1 handler transaction in a single
    /// block.
    /// Calling `get_txs` or `validate_tx` tags the touched transactions with the current block
    /// counter, so that further calls will know not to touch them again.
    /// At the start and end (commit) of every block, the counter is incremented, thus "unstaging"
    /// all tagged transactions from the previous block attempt.
    // TODO(Gilad): remove "for rejected" from name when uncommitted is migrated to records DS.
    current_staging_epoch: StagingEpoch,
    /// All consumed transactions that are waiting to be removed from the transaction manager.
    /// Invariant: Ordered lexicographically by the block timestamp where they were marked as
    /// consumed, then order-of-arrival for identical timestamps.
    /// Invariant 2: A transaction is in the queue iff it is in the records and marked as consumed.
    consumed_queue: BTreeMap<BlockTimestamp, Vec<TransactionHash>>,
}
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L168-209)
```rust
    /// Adds a transaction to the transaction manager, return true if the transaction was
    /// successfully added. If the transaction is occupied or already had its hash stored as
    /// committed, it will not be added, and false will be returned.
    // Note: if only the committed hash was known, the transaction will "fill in the blank" in the
    // committed txs storage, to account for commit-before-add tx scenario.
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L351-353)
```rust
    fn create_record_if_not_exist(&mut self, hash: TransactionHash) -> bool {
        self.records.insert(hash, TransactionRecord::new(hash.into()))
    }
```
