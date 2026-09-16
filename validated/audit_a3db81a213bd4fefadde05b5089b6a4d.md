### Title
Unbounded accumulation of L1 handler transaction records that are never freed until an L1-side cancellation/consumption event arrives - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager` keeps every scraped L1→L2 message (`LogMessageToL2`) in an in-memory `records: Records` map (an `IndexMap`) for the lifetime of the process, and only removes a record when a *matching L1-side event* later arrives: `finalize_cancellation` (triggered by `MessageToL2Canceled`) or the lazy sweep in `clear_old_tx_from_consumed_queue` (triggered by `ConsumedMessageToL2`, and only after `consume_tx` is called again for some other tx once the timelock passes). This is structurally the same bug class as CVE-2024-38534: a per-flow/per-transaction record is created eagerly on ingestion but is only ever freed by a *correlated response event*, so if that correlated event is delayed or never sent, the record is retained forever, in memory, with no independent cap.

### Finding Description
`TransactionManager::add_tx` (`crates/apollo_l1_events/src/transaction_manager.rs:173-209`) creates a `TransactionRecord` for every `LogMessageToL2` event scraped from L1, unconditionally, via `create_record_if_not_exist`: [1](#0-0) 

Once created, the record's lifecycle only permits state transitions (`Pending → Committed`, `Pending → Rejected`, `Pending → CancellationStartedOnL2 → CancelledOnL2`, `* → Consumed`) but the record is never dropped from `self.records` as a result of any of these transitions alone: [2](#0-1) 

The only two code paths that actually call `self.records.remove(...)` are:
1. `finalize_cancellation`, which requires the L1 sender to have first called `request_cancellation` and then to wait out `l1_handler_cancellation_timelock_seconds` before an L1 `MessageToL2Canceled` event is scraped: [3](#0-2) 

2. `clear_old_tx_from_consumed_queue`, which only removes a record after it has been marked `Consumed` (via a `ConsumedMessageToL2` L1 event) and only lazily, when a *subsequent* `consume_tx` call happens to run past `l1_handler_consumption_timelock_seconds`: [4](#0-3) 

There is no size cap, TTL sweep, or eviction policy on `records` independent of these two externally-triggered events. A transaction that is `Committed` or `Rejected` on L2 but for which the corresponding L1 `ConsumedMessageToL2`/`MessageToL2Canceled` event is delayed, dropped, or never emitted (e.g. the sender never calls the L1 cancel function, and the block-building/consumption bookkeeping on L1 does not immediately correlate) stays resident in the `records` `IndexMap` indefinitely: [5](#0-4) 

This mirrors the Suricata modbus bug class exactly: a per-"transaction" tracking structure is populated on the request side, and freed only on receipt of a correlated response-side event; if the response side never arrives, the tracking structure accumulates unboundedly for the life of the flow/process.

### Impact Explanation
Since `LogMessageToL2` is emitted by any unprivileged L1 account calling the Starknet core contract's `sendMessageToL2`, an attacker fully controls the rate at which new `TransactionRecord`s are created in the sequencer's `apollo_l1_events` service. If the attacker simply never triggers/relies on cancellation (never calls the L1 cancel path) and the messages are not registered as consumed quickly (e.g., they target invalid/reverting L2 entry points and are repeatedly `Rejected` at commit time rather than `Consumed`), the records persist in `records` forever, growing the in-memory map and the co-located `proposable_index`/`consumed_queue` bookkeeping without bound. Sustained abuse leads to unbounded memory growth in the sequencer's L1-events component, which can crash or degrade that service, disrupting L1→L2 message intake and, by extension, block production availability — a resource-exhaustion/denial-of-service impact analogous to the CVE-2024-38534 modbus leak.

### Likelihood Explanation
The trigger requires only ordinary, unprivileged use of the Starknet core contract's L1→L2 message-sending function; no special privileges, race conditions, or protocol violations are required. The cost is bounded by L1 gas, but L1 gas cost does not scale with the amount of sequencer-side memory consumed per message, so the attack is asymmetric (cheap L1 calls vs. unbounded sequencer memory retention), making it a realistic amplification vector for a persistent attacker with a modest L1 gas budget.

### Recommendation
Add an independent, time-based eviction policy for `records` that does not depend on receiving a correlated `ConsumedMessageToL2`/`MessageToL2Canceled` event — e.g., cap total record count/memory, or forcibly age out `Committed`/`Rejected` records after a bounded retention window regardless of consumption/cancellation status, with appropriate reconciliation if a late L1 event arrives afterward. This is the same mitigation class used for the Suricata fix (bounding reassembly/tracked-transaction depth) and for the referenced CVE remediation (limiting `stream.reassembly.depth`)-analogous bounded state.

### Proof of Concept
1. From an L1 account, repeatedly call the Starknet core contract's `sendMessageToL2` with a large `nonce`/payload targeting an L2 entry point/selector that will cause the resulting `L1HandlerTransaction` to be `Rejected` at commit time (e.g., an invalid selector or one that always reverts), and never call the L1 cancellation function for these messages.
2. Each such call causes the L1 scraper to invoke `TransactionManager::add_tx` (`crates/apollo_l1_events/src/transaction_manager.rs:173`), inserting a new `TransactionRecord` into `records`.
3. On block commit, `commit_txs` marks these as `Rejected` (`crates/apollo_l1_events/src/transaction_manager.rs:158-165`, `crates/apollo_l1_events/src/transaction_record.rs:63-72`) — the record remains in `records` since no removal path is triggered by rejection.
4. Repeat indefinitely; since no `MessageToL2Canceled`/`ConsumedMessageToL2` event ever correlates with these hashes, `records` grows without bound, consistent with the class of unbounded-per-transaction-state-retention bug reported in CVE-2024-38534.

(Note: full certainty about exactly which real-world L1 core-contract conditions prevent a `ConsumedMessageToL2` event from ever firing for a rejected/committed message could not be verified from this repository alone, since the L1 core contract implementation is out of scope here; the finding is based on the sequencer-side code's explicit removal conditions, which are unconditionally gated on these external events.)

### Citations

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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L173-181)
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
