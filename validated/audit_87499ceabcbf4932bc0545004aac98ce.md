### Title
L1 handler transactions rejected during block building are permanently orphaned in the L1 events `TransactionManager` records map, causing unbounded memory growth and permanently stuck L1→L2 messages - (File: `crates/apollo_l1_events/src/transaction_manager.rs`)

### Summary
`TransactionManager::commit_txs` marks any L1 handler transaction that gets rejected from a block as `TransactionState::Rejected` but never removes it from the `records` map, and the transaction is never re-added to the `proposable_index`. Unlike cancellation/consumption, which explicitly call `records.remove(&tx_hash)`, a rejected L1 handler transaction has no code path back to `Pending` or out of `records`. Each L1 message that a proposer's bouncer/batcher excludes from a block (e.g. due to resource-capacity limits) therefore becomes a permanent, un-collectible entry, matching the reported bug class of "rejected channel/resource state never cleaned up."

### Finding Description
`TransactionManager` documents that its `records` map is only meant to retain transactions "until they can be safely removed, like when they are consumed on L1, or fully cancelled on L1" [1](#0-0) . Consistent with that, `finalize_cancellation` and normal consumption flows call `self.records.remove(&tx_hash)` [2](#0-1) .

However, `commit_txs` handles rejected transactions completely differently — it only mutates the record's state via `mark_rejected()` and never removes it from `records`: [3](#0-2) 

`mark_rejected` sets `state = TransactionState::Rejected` permanently, with no other code path transitioning a `Rejected` record back to `Pending`: [4](#0-3) 

`is_proposable()` only returns true for `TransactionState::Pending` [5](#0-4) , and `maintain_indices` removes non-proposable transactions from `proposable_index` but never re-adds a `Rejected` transaction later [6](#0-5) . The `snapshot()` function even documents the invariant that `Rejected` transactions remain permanently in `records`, contrasting with `CancellationFinalizedOnL1` which panics because it's assumed to always be removed [7](#0-6) .

The rejection path is reached from `Batcher::commit_proposal_and_block`, which filters `rejected_tx_hashes` for L1 handler transactions and forwards them to the L1 events provider's `commit_block`, which calls `tx_manager.commit_txs(committed, rejected)`: [8](#0-7) 

This rejection occurs whenever the bouncer/block builder cannot fit an L1 handler transaction into a block (e.g., resource/weight limits), which is a normal, attacker-influenceable outcome of sending enough L1→L2 messages to exceed per-block L1-handler capacity — no malicious proposer or operator behavior is required.

### Impact Explanation
Every rejected L1 handler transaction leaves a permanent, unremovable entry in `TransactionManager::records` on every sequencer node (each node independently scrapes and processes the same L1 events, so the leak affects the whole network, not just one operator). This is directly analogous to the reported `golang.org/x/crypto` SSH channel-rejection leak: rejected channel/resource state that should be released is instead retained forever, driving unbounded memory growth that can eventually crash the process — here, crashing sequencer nodes prevents the network from confirming new transactions.

Beyond the memory-growth DoS, the rejected transaction is also functionally stuck: since it is not `Pending`, it will never again appear in `proposable_index` and will never be retried by `get_txs`, so the underlying L1→L2 message (e.g. an L1 bridge deposit) is never delivered on L2. The only way for the L1 sender to recover is to pay for and wait through the full L1-initiated cancellation flow, meaning the deposited/message-carried value is effectively frozen until that costly out-of-band remediation completes.

### Likelihood Explanation
An L1 message sender does not need any special privileges to trigger a rejection — any account can send enough L1→L2 messages (each is a normal, unprivileged L1 transaction) to exceed the sequencer's per-block bouncer/resource capacity for L1 handler transactions, causing some of those messages to be excluded from a block and marked `Rejected`. This is achievable through ordinary usage/congestion and does not require a malicious proposer, operator, or peer.

### Recommendation
Treat `Rejected` L1 handler transactions the same way `CancellationFinalizedOnL1` transactions are treated: either remove them from `records` immediately (if permanent exclusion is intended and the sender must rely on L1 cancellation) or, if resubmission/retry is intended, transition the record back to `Pending` and re-add it to `proposable_index` so it can be proposed again. Regardless of which semantics are correct, `records` must not retain entries indefinitely for states that have no further code path to removal or reactivation.

### Proof of Concept
1. An attacker (or organic congestion) sends enough L1→L2 messages that the sequencer's block builder cannot fit all pending L1 handler transactions into consecutive blocks, causing the bouncer/batcher to reject some of them.
2. `Batcher::commit_proposal_and_block` forwards these hashes as `rejected_tx_hashes`, filtered to L1-handler ones, to `L1EventsProviderClient::commit_block` [8](#0-7) .
3. `TransactionManager::commit_txs` marks each as `Rejected` via `mark_rejected()` but never removes them from `records` [9](#0-8) .
4. Repeating this indefinitely (each new batch of L1 messages that gets rejected) grows `records` without bound on every sequencer node, while the underlying messages are permanently excluded from `proposable_index` and never retried.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L29-33)
```rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransactionManager {
    /// Storage of all l1 handler transactions --- keeps transactions until they can be safely
    /// removed, like when they are consumed on L1, or fully cancelled on L1.
    pub records: Records,
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L301-331)
```rust
        for (&tx_hash, record) in self.records.iter() {
            match record.state {
                TransactionState::Rejected => {
                    snapshot.rejected.push(tx_hash);
                    if self.is_staged(tx_hash) {
                        snapshot.rejected_staged.push(tx_hash);
                    }
                }
                TransactionState::Committed => {
                    snapshot.committed.push(tx_hash);
                }
                TransactionState::Pending => {
                    snapshot.uncommitted.push(tx_hash);
                    if self.is_staged(tx_hash) {
                        snapshot.uncommitted_staged.push(tx_hash);
                    }
                }
                TransactionState::CancellationStartedOnL2 => {
                    snapshot.cancellation_started_on_l2.push(tx_hash);
                }
                TransactionState::CancelledOnL2 => {
                    snapshot.cancelled_on_l2.push(tx_hash);
                }
                // This should never happen, fully cancelled txs are removed from the transaction
                // manager.
                TransactionState::CancellationFinalizedOnL1 => {
                    panic!(
                        "This should never happen, fully cancelled txs are removed from the \
                         transaction manager."
                    );
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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L62-72)
```rust
    // Note: double reject not currently checked.
    pub fn mark_rejected(&mut self) {
        // Pedantic, this is unlikely to happen.
        assert!(
            !self.committed,
            "Attempted to reject a committed transaction {}",
            self.tx.tx_hash()
        );
        self.state = TransactionState::Rejected;
        self.rejected = true;
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L155-157)
```rust
    pub fn is_proposable(&self) -> bool {
        matches!(self.state, TransactionState::Pending)
    }
```

**File:** crates/apollo_batcher/src/batcher.rs (L1181-1191)
```rust
        // Notify the L1 provider of the new block.
        let rejected_l1_handler_tx_hashes = rejected_tx_hashes
            .iter()
            .copied()
            .filter(|tx_hash| consumed_l1_handler_tx_hashes.contains(tx_hash))
            .collect();

        let l1_events_provider_result = self
            .l1_events_provider_client
            .commit_block(consumed_l1_handler_tx_hashes, rejected_l1_handler_tx_hashes, height)
            .await;
```
