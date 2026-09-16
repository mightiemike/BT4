### Title
Committed L1 handler transactions can have their records erased by a late L1 cancellation finalization - ([File: crates/apollo_l1_events/src/transaction_manager.rs])

### Summary
`TransactionManager::finalize_cancellation` removes a transaction's record from the provider's bookkeeping whenever a `TransactionCanceled` L1 event is scraped, **without checking whether the transaction was already committed to L2**. This mirrors the reported bug class ("an action that already progressed/completed can still be undone by a late cancellation"): once an L1Handler transaction is `Committed` on L2, the record should be immutable with respect to cancellation (as is explicitly enforced elsewhere in the same module), but `finalize_cancellation` only logs a warning and proceeds to delete it anyway.

### Finding Description
The transaction lifecycle is tracked with a `TransactionState` enum, and the code explicitly documents that committing should be irreversible with respect to cancellation: [1](#0-0) 

`update_time_based_state` deliberately returns early ("Committing overrides cancellations") if `self.committed` is true, preventing a `CancellationStartedOnL2` record from silently drifting into `CancelledOnL2` once it has been committed.

However, `finalize_cancellation`, which is invoked when the scraper observes the terminal `TransactionCanceled` L1 event, has no equivalent guard: [2](#0-1) 

It only warns if `record.state != TransactionState::CancellationStartedOnL2` (which is true for a `Committed` record too) and then unconditionally calls `mark_cancellation_finalized_on_l1()` and `self.records.remove(&tx_hash)`. `mark_cancellation_finalized_on_l1()` itself has no committed-check either: [3](#0-2) 

Because L2 block commitment (via `commit_txs`/`apply_commit_block`) happens well before the corresponding state update/proof reaches L1, there is a real window where a tx is `Committed` in the local `TransactionManager` while the L1 core contract still considers the message uncomsumed and lets a previously-started cancellation finalize. When the resulting `TransactionCanceled` event is scraped and fed through `add_events`, it is routed straight to `finalize_cancellation` with no committed-state protection: [4](#0-3) 

Deleting the record for an already-`Committed` transaction erases the only source of truth used to detect duplicate/retried commit blocks during catch-up: [5](#0-4) 

`is_committed` simply looks up the (now-missing) record: [6](#0-5) 

If that node later needs to re-process (or is resynced/rolled back to) the same or an earlier height containing this tx hash, `is_committed` incorrectly reports `false`. In the `Less` (past-height replay) branch this causes the node to conclude the replay contains "DIFFERENT transaction hashes" than what it already committed and to return `UnexpectedHeight`, which re-triggers `start_catching_up`. Because the record is permanently gone (it is never recreated as committed except by re-processing a `commit_block` for that hash at the *current* height), the node can become stuck oscillating between catch-up and this same error whenever this height/backlog combination recurs, diverging from honest nodes that never lost the committed record and stalling this node's ability to validate/propose new blocks that depend on correct L1Handler bookkeeping.

### Impact Explanation
This breaks the same invariant the report's bug class targets: state that has already reached a terminal/committed step is nonetheless mutated by a later "cancel" action, because the guard (`is_committed`) that protects this transition everywhere else in the module (`mark_cancellation_request`, `update_time_based_state`) is missing in `finalize_cancellation`. The consequence inside this codebase is loss of the committed-transaction bookkeeping used for reorg/duplicate detection, which can put an honest node into a persistent catch-up error loop (liveness/divergence), and, combined with the fact that L1-side consumption normally lags L2 commitment by the proving/state-update delay, it also means a legitimate refund/cancellation can complete on L1 for a message whose L2-side effects were already executed and committed.

### Likelihood Explanation
The trigger (starting then finalizing an L1 message cancellation) is fully controlled by the unprivileged L1 message sender, requiring no special privileges — the only precondition is that the sequencer's L2 commitment of the tx happens fast enough relative to the (long, day-scale) L1 cancellation delay, which is entirely plausible since state-update/proof submission to L1 for a committed block is typically much slower than a single block's commit time.

### Recommendation
Add a committed-state guard in `finalize_cancellation` (and `mark_cancellation_finalized_on_l1`) mirroring the one already used in `mark_cancellation_request`/`update_time_based_state`: if the record `is_committed()`, refuse to remove/mutate it (log at error/warn level and skip), so a `TransactionCanceled` event can never erase bookkeeping for a transaction that has already been committed on L2.

### Proof of Concept
1. An L1 message sender sends an L1→L2 message; the scraper adds it to `TransactionManager` as `Pending`.
2. The sequencer proposes and commits a block including this L1Handler tx; `commit_txs` marks the record `Committed` (`committed = true`).
3. Before the corresponding L2 state update/proof reaches L1 (a normally much longer delay), the sender's earlier-started L1 cancellation request finalizes on the L1 core contract, and the scraper reports a `TransactionCanceled` event.
4. `add_events` routes this to `tx_manager.finalize_cancellation(tx_hash)`, which only warns about the unexpected state and then unconditionally removes the record: [7](#0-6) 
5. `tx_manager.is_committed(tx_hash)` now returns `false` for a transaction that was, in fact, committed, corrupting duplicate-detection logic used during catch-up (`accept_commit_while_catching_up`, `Less` branch).

### Citations

**File:** crates/apollo_l1_events/src/transaction_record.rs (L103-109)
```rust
    /// Mark a transaction as cancelled on L1.
    // This happens just before the tx is removed from the transaction manager.
    // It is useful to change the state so that maintain_indices can remove it from the proposable
    // index.
    pub fn mark_cancellation_finalized_on_l1(&mut self) {
        self.state = TransactionState::CancellationFinalizedOnL1;
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L286-288)
```rust
    pub fn is_committed(&self, tx_hash: TransactionHash) -> bool {
        self.records.get(&tx_hash).is_some_and(|record| record.is_committed())
    }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L170-176)
```rust
                Event::TransactionCanceled { tx_hash } => {
                    info!(
                        "Cancellation finalized for tx_hash: {tx_hash}. Deleting the tx from the \
                         provider records."
                    );
                    self.tx_manager.finalize_cancellation(tx_hash);
                }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L398-429)
```rust
        match new_height.cmp(&current_height) {
            // This is likely a bug in the batcher/sync, it should never be _behind_ the provider.
            Less => {
                // TODO(guyn): check if this is reliable: old blocks can have txs that were
                // committed then consumed and deleted. We should probably decide to always log and
                // ignore old blocks or always return an error.
                let diff_from_already_committed: Vec<_> = committed_txs
                    .iter()
                    .copied()
                    .filter(|&tx_hash| !self.tx_manager.is_committed(tx_hash))
                    .collect();

                if diff_from_already_committed.is_empty() {
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, and all committed transaction hashes already known to be \
                         committed."
                    );
                    return Ok(());
                } else {
                    // This is either a configuration error or a bug in the
                    // batcher/sync/catching up code.
                    error!(
                        "Duplicate commit block: commit block for {new_height:?} already \
                         received, with DIFFERENT transaction_hashes: \
                         {diff_from_already_committed:?}"
                    );
                    Err(L1EventsProviderError::UnexpectedHeight {
                        expected_height: current_height,
                        got: new_height,
                    })?
                }
```
