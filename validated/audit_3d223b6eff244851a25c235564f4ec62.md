### Title
Unhandled-record panic on L1 cancellation-request event before transaction is scraped - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager::request_cancellation` uses `.expect(...)` to unwrap the result of looking up a transaction record, and will panic if no record exists for the given `tx_hash`. Its sibling functions that process the same class of "come from L1, may reference an unscraped tx" event (`finalize_cancellation`, `consume_tx`) explicitly handle the missing-record case by logging and returning `Ok`/`()` instead of panicking, showing this exact scenario ("transaction too old to be scraped") is an anticipated and legitimate condition in production.

### Finding Description
`request_cancellation` is the only lifecycle-transition entry point in `TransactionManager` that does not defensively check for record existence before mutating state: [1](#0-0) 

Compare this to `finalize_cancellation`, which is invoked on the very same cancellation lifecycle path (cancellation is a two-step L1 process: request, then finalize) and explicitly tolerates a missing record with a log message, calling out that "this can happen if the transaction was too old to be scraped (e.g. it was created before we started scraping)": [2](#0-1) 

and `consume_tx`, which handles the identical missing-record condition with a `debug!` log and an early return: [3](#0-2) 

Because the L1-handler record is only created when the corresponding message-to-L2 event is scraped (`add_tx` / `create_record_if_not_exist`), and the cancellation-request event is scraped from a separate L1 contract log stream, a `tx_hash` referenced by a cancellation-request event can legitimately have no corresponding record in `records` — precisely the scenario the code comments for `finalize_cancellation`/`consume_tx` describe. `request_cancellation`, however, propagates that `None` straight into an `.expect()` panic instead of degrading gracefully like its siblings. [4](#0-3) 

### Impact Explanation
A panic inside the transaction manager's cancellation-request handling crashes the L1 events provider component, which is part of the sequencer process handling propose/validate transitions for L1 handler transactions. A crashed node stops confirming new transactions/blocks, matching the "network unable to confirm new transactions" impact bar. This is analogous to CVE-2021-4023's bug class: a cancellation operation that is improperly handled during a state condition (here, a not-yet-scraped/absent record, there, a resource shortage) triggers a kernel/process panic.

### Likelihood Explanation
Any user can send an L1-to-L2 message and subsequently invoke the corresponding cancellation-request function on the L1 core contract — no privileged access is required. The panic is triggered purely by ordering/timing between when the sequencer's scraper catches up to the "message sent" event versus the "cancellation requested" event (e.g., a node restarting its scraper from a checkpoint after the message was sent but concurrently with, or before, ingesting the cancellation event, or any race where the cancellation event is processed prior to the corresponding creation event). The code's own comments on the sibling functions acknowledge this "transaction too old to be scraped" case is a real, expected occurrence, indicating non-trivial likelihood in production rather than a purely theoretical race.

### Recommendation
Change `request_cancellation` to mirror `finalize_cancellation`/`consume_tx`: check for record existence first, and if absent, log (e.g., `info!`/`warn!`) and return `None` instead of using `.expect()` to panic.

### Proof of Concept
1. An L1 user sends a message to L2 (creating an L1 handler transaction with hash `H`) and then, before the sequencer's scraper has ingested/created the record for `H` (e.g. due to scraper lag, restart from a later checkpoint, or reordering across the two contract event streams), invokes the L1 contract's cancellation-request function for the same message.
2. When the L1 events scraper processes the cancellation-request event before (or without ever) processing the message-creation event for `H`, `TransactionManager::request_cancellation(H, ...)` is invoked with no existing record. [1](#0-0) 
3. `with_record` returns `None` (record absent), and the surrounding `.expect(...)` panics, crashing the l1_events_provider/sequencer process.

### Citations

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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L221-229)
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
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L252-262)
```rust
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
