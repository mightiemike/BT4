### Title
L1-handler transaction records that were fully cancelled or consumed on L1 can be resurrected to `Pending` state by a re-scraped `L1HandlerTransaction` event, bypassing the cancellation/consumption finality checks - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager::add_tx` (and `commit_txs`) create a brand-new, default-state (`Pending`) `TransactionRecord` whenever no record currently exists for a given `tx_hash`, via `create_record_if_not_exist`. Because terminal states are implemented by **removing** the record from `self.records` (`finalize_cancellation` and `clear_old_tx_from_consumed_queue`), a later re-delivery of the original `Event::L1HandlerTransaction` for that same hash silently re-creates the record as fresh `Pending`, erasing the fact that the message was already cancelled on L1 or already consumed/executed. This mirrors the `canOffboard[term]` re-trigger bug: a terminal/"cleaned-up" state is not permanently remembered, and a subsequent (stale/duplicate) message can put the entity back into an active state that bypasses the finality/authorization check (`is_validatable()`).

### Finding Description
`L1EventsProvider::add_events` dispatches `Event::L1HandlerTransaction` to `TransactionManager::add_tx` unconditionally: [1](#0-0) 

`add_tx` calls `create_record_if_not_exist`, which inserts a fresh, default (`TransactionState::Pending`) record if the hash is absent from `records`: [2](#0-1) [3](#0-2) 

Terminal states are not tombstoned — they are physically deleted:
- `finalize_cancellation` calls `self.records.remove(&tx_hash)` after marking `CancellationFinalizedOnL1`: [4](#0-3) 
- `clear_old_tx_from_consumed_queue` (invoked from `consume_tx`) removes records once the consumption timelock passes: [5](#0-4) 

`TransactionRecord::default()` initializes `state = TransactionState::Pending`, which passes `is_validatable()`/`is_proposable()`: [6](#0-5) [7](#0-6) 

Once `add_tx` is called for a hash that has been removed from `records`, `create_record_if_not_exist` returns `true` ("new record"), and the payload-matching arm in `add_tx` treats it as brand new, sets the `Full` payload, and `maintain_indices` re-inserts it into `proposable_index` (since `is_proposable()` is true for `Pending`), making it eligible again for `get_txs` (proposal) and `validate_tx` (validation), i.e., the exact opposite of the guarantee documented at the top of `TransactionManager`: *"Invariant 2: Once removed from this index, a transaction will never be proposed again."*

The trigger event is the same `Event::L1HandlerTransaction` that originally created the record — this is derived directly from an L1 `LogMessageToL2` event, i.e., something an ordinary L1 message sender emits by calling the Starknet core contract on L1 (`sendMessageToL2`), which the scraper later turns into this event via `Event::from_l1_event`: [8](#0-7) 

If the L1 scraper (re)delivers this event a second time for the same L1 message hash after the record has already been purged — e.g., due to node restart/resync re-scraping an L1 block range that was already processed, catch-up from state-sync re-initializing with a stale window, or any overlap in the scraper's scanned range that includes a block whose event was already fully processed and cleaned up — the resulting `add_tx` call resurrects the transaction as `Pending`, even though the L1 sender already cancelled the message (finalized cancellation on L1) or the message was already consumed/executed on L2.

### Impact Explanation
- If the resurrected transaction corresponds to an L1 message the **sender already cancelled** on L1 (finalized), the sequencer will treat it as a fresh, proposable/validatable L1-handler transaction again, and may include it in a block for execution — despite the core L1 contract's `cancelL1ToL2Message` semantics implying it should never be executable again. This causes the node to propose/validate a transaction that should be permanently rejected, which can cause validator disagreement (a node with a stale/duplicated scrape state validates it as `Validated`, while a fresh node correctly rejects it), i.e., honest-node divergence in what constitutes a valid block.
- If the resurrected transaction corresponds to an L1 message that was **already consumed** (executed) on L2 and later removed from the consumed queue after the timelock, its record is wiped from `records`; a stale re-scrape would then re-create it as `Pending`. There is a defense-in-depth assertion (`assert!(is_new_entry, "Duplicate L1 handler transaction hash…")` in `apollo_batcher/src/block_builder.rs`) and the underlying execution layer's L1-to-L2 message nonce consumption (`consume_l1_to_l2_message`, `crates/apollo_starknet_os_program/.../transaction_impls.cairo`), which are expected to reject a genuine double-consumption at the state/OS level. This limits (but does not eliminate) the worst-case impact to node-level liveness/consensus-agreement issues (a validator panicking on the duplicate-hash assertion, or nodes disagreeing on whether the transaction is currently valid to propose/validate) rather than an actual double execution — this residual protection could not be fully verified line-by-line for every code path within the given exploration budget.
- At minimum, this breaks the stated invariant of the transaction manager ("once removed... never proposed again") and reopens transactions that the protocol intends to be permanently finalized, directly paralleling the `canOffboard[term]` re-trigger bug (a "cleaned up" terminal flag becoming re-triggerable by a stale/duplicate message).

### Likelihood Explanation
Likelihood is moderate: it requires the L1 event scraper to (re)deliver an already-processed `LogMessageToL2` event for a hash whose record has since been purged (via full cancellation or the consumption timelock). This is plausible under scraper restart/catch-up/resync scenarios or state-sync re-initialization with an overlapping range, not merely a malicious-operator scenario — an ordinary L1 message sender only needs to send a message and then legitimately cancel it (or wait for consumption + timelock) for the record to be removed; the resurrection itself depends on the scraper/sync path re-emitting the historical event, which is an internal correctness property rather than requiring privileged/malicious actors.

### Recommendation
- Do not delete `TransactionRecord`s outright on finalization (`finalize_cancellation`, `clear_old_tx_from_consumed_queue`). Instead, retain a permanent tombstone/terminal marker (e.g., keep the record with `CancellationFinalizedOnL1`/`Consumed`-and-purged state, or maintain a separate "finalized hashes" set) so that `create_record_if_not_exist`/`add_tx` can detect and reject/ignore re-delivery of an `L1HandlerTransaction` event for an already-finalized hash instead of creating a fresh `Pending` record.
- Alternatively, have `add_tx` consult a durable append-only "seen and finalized" index (independent of `records`) before creating a new record, so a duplicate/replayed scrape event can never regress a finalized transaction back to `Pending`.

### Proof of Concept
1. Send an L1-to-L2 message (`LogMessageToL2`) creating `tx_hash = H`; the scraper turns this into `Event::L1HandlerTransaction` and `add_tx` creates record `H` as `Pending`.
2. The sender cancels the message on L1: `TransactionCancellationStarted` then, after the L1 timelock, `TransactionCanceled` is scraped; `finalize_cancellation` sets state `CancellationFinalizedOnL1` and calls `self.records.remove(&H)` — `H` is now fully gone from `records` and `proposable_index` (see `crates/apollo_l1_events/src/transaction_manager.rs:221-245`).
3. Due to a scraper restart, resync, or overlapping re-scan window, the original `LogMessageToL2` log for `H` is (re)fetched from L1 and converted again into `Event::L1HandlerTransaction`.
4. `add_events` -> `TransactionManager::add_tx(H, …)` is called; `create_record_if_not_exist` finds no existing record and inserts a fresh default record with `state = Pending` (`crates/apollo_l1_events/src/transaction_record.rs:269-279`, default `TransactionState::Pending`); `maintain_indices` re-adds `H` to `proposable_index` because `record.is_proposable()` is true.
5. `H` is now proposable/validatable again (`get_txs`/`validate_tx` will treat it as `Validated`), even though it was fully cancelled by its sender on L1 in step 2 — directly contradicting the stated invariant "Once removed from this index, a transaction will never be proposed again" documented at `crates/apollo_l1_events/src/transaction_manager.rs:38-39`.

### Citations

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L135-143)
```rust
        for event in events {
            match event {
                Event::L1HandlerTransaction {
                    l1_handler_tx,
                    block_timestamp,
                    scrape_timestamp,
                } => {
                    self.tx_manager.add_tx(l1_handler_tx, block_timestamp, scrape_timestamp);
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L351-353)
```rust
    fn create_record_if_not_exist(&mut self, hash: TransactionHash) -> bool {
        self.records.insert(hash, TransactionRecord::new(hash.into()))
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

**File:** crates/apollo_l1_events_types/src/lib.rs (L310-320)
```rust
impl Event {
    pub fn from_l1_event(
        chain_id: &ChainId,
        l1_event: L1Event,
        scrape_timestamp: UnixTimestamp,
    ) -> Result<Self, StarknetApiError> {
        Ok(match l1_event {
            L1Event::LogMessageToL2 { tx, fee, block_timestamp, .. } => {
                let tx = L1HandlerTransaction::create(tx, chain_id, fee)?;
                Self::L1HandlerTransaction { l1_handler_tx: tx, block_timestamp, scrape_timestamp }
            }
```
