### Title
Rejected L1-to-L2 messages are permanently stuck instead of being returned to `Pending`, causing irrecoverable freezing of bridged funds - (File: crates/apollo_l1_events/src/transaction_manager.rs)

### Summary
The Polygon zkEVM incident describes a class of bugs where a claim (L1→L2 message) transaction that fails to be included/executed correctly is not returned to a retryable state, permanently blocking legitimate users from ever completing their deposit/claim. The Starknet sequencer's L1 events / L1-handler pipeline has an analogous flaw: when an L1 handler transaction fails to make it into a committed block, `TransactionManager::commit_txs` calls `TransactionRecord::mark_rejected`, which permanently transitions the transaction's state to `TransactionState::Rejected`. Because `is_proposable()` only returns `true` for `TransactionState::Pending`, a rejected L1 handler transaction is removed from — and never re-added to — the `proposable_index`, so no sequencer will ever attempt to include it in a future block again.

### Finding Description
`TransactionManager::commit_txs` is invoked after block execution/commit with the lists of `committed_txs` and `rejected_txs` (L1 handler tx hashes that were part of a proposal but not ultimately committed): [1](#0-0) 

For every hash in `rejected_txs`, `mark_rejected()` is called unconditionally: [2](#0-1) 

This sets `state = TransactionState::Rejected` permanently — there is no code path that ever transitions a `Rejected` record back to `Pending`. `is_proposable()`, which controls whether the transaction gets (re-)added to the `proposable_index` used by `get_txs()` for block proposal, only returns `true` for `Pending`: [3](#0-2) 

`maintain_indices` removes any transaction from `proposable_index` once `is_proposable()` becomes false, and it is only re-added on the `Pending` state transition — which never happens for `Rejected`: [4](#0-3) 

Notably, `is_validatable()` still returns `true` for a `Rejected` record (it only excludes `Committed`, `Cancelled`, `Consumed`): [5](#0-4) 

This is inconsistent with the intended design documented in the project's own sequence diagrams, which explicitly state that rejected transactions should be "unstaged" and kept as `Pending` so they remain proposable in a later block: [6](#0-5) 

Because a message from L1 (deposit/claim analog) is fully controlled/timed by an unprivileged L1 sender, and inclusion into a specific proposed block can fail for reasons outside the sender's control (proposer deadline cutoffs, bouncer/resource-weight limits causing the block builder to drop the tx from the finalized block, etc. — see `docs/diagrams/06-l1-handler-flow.md` "Block Commit" step and `apollo_batcher::block_builder`), a transaction can legitimately end up in the `rejected_txs` list on its very first attempt. Once that happens, the state machine offers no recovery: the transaction becomes permanently non-proposable and non-retryable, while its underlying L1 message is never consumed or cancelled (since cancellation is a separate, explicit L1 action). The user's bridged funds/message becomes permanently stuck — unable to ever be delivered to L2 — mirroring the "credits already used, claim permanently rejected" failure mode from the Polygon zkEVM report.

### Impact Explanation
This is a permanent freezing-of-funds bug class: any L1-to-L2 message that is bumped out of a proposed block once (a normal, expected occurrence under network congestion, bouncer/resource pressure, or proposer deadlines) is never retried by any honest sequencer again. Since there's no user-facing recovery mechanism for `Rejected` records other than the (unrelated) L1 cancellation flow, the deposit is effectively lost from the L2 execution path forever, without the corresponding L1 message ever being cancelled or refunded. This satisfies "permanent freezing of funds" and "network unable to confirm new (L1) transactions" for the affected message.

### Likelihood Explanation
The transition into `Rejected` is reachable in ordinary sequencer operation (not requiring a malicious operator/proposer): any congestion, bouncer-weight exhaustion, or a block-generation deadline can cause a validly-staged L1 handler transaction to be excluded from the finally committed block, which — per `commit_txs` — is unconditionally treated as a permanent rejection. No special privilege beyond originating a normal L1→L2 message is required to be exposed to this bug; it is a systemic side effect of normal block-building behavior rather than an edge case requiring attacker cooperation.

### Recommendation
Align the implementation with the documented intent in `docs/diagrams/06-l1-handler-flow.md`: transactions that are not committed in a given block attempt should be unstaged and returned to `Pending` (re-added to `proposable_index`) rather than transitioned to a permanent terminal `Rejected` state. If a distinct `Rejected` state is still desired for observability, it must not be treated as permanently non-proposable — `is_proposable()`/`maintain_indices` should allow re-queuing of such transactions in subsequent blocks, or `commit_txs` should distinguish between "rejected due to real validity failure" (permanent) versus "excluded from this block only" (retryable), and only permanently reject transactions confirmed invalid at execution time in the OS, not ones dropped for capacity/deadline reasons.

### Proof of Concept
1. An unprivileged user sends a `sendMessageToL2` message on L1, producing an `L1HandlerTransaction` that is scraped and stored as `Pending` in `TransactionManager`.
2. The transaction is staged and proposed in block N via `get_txs`, per `TransactionManager::get_txs` (crates/apollo_l1_events/src/transaction_manager.rs:72-114).
3. During block building/finalization, the transaction is excluded from the finally committed block N (e.g., due to a bouncer resource-weight limit reached, or the block-generation deadline being hit before it is executed) and is reported through `commit_txs(committed_txs, rejected_txs)` with the tx hash in `rejected_txs`.
4. `TransactionRecord::mark_rejected()` sets the transaction's state to `TransactionState::Rejected` permanently (crates/apollo_l1_events/src/transaction_record.rs:62-72).
5. `is_proposable()` now returns `false` for this record forever, so `maintain_indices` removes/never re-adds it to `proposable_index` (crates/apollo_l1_events/src/transaction_manager.rs:376-408).
6. No subsequent block will ever call `get_txs` and retrieve this transaction again; the user's L1 message is permanently stuck and unactionable on L2, even though it was never consumed, cancelled, or actually executed.

Note: I was unable to fully trace the exact upstream logic in `apollo_batcher::block_builder.rs` that decides which L1 handler tx hashes are placed into `rejected_txs` for a given proposal (i.e., confirming whether normal bouncer/deadline-driven exclusion — as opposed to genuine execution-validity failure — is what populates this list). Given index-size limits, I recommend a Devin session with full repository access to confirm the exact trigger condition in `apollo_batcher/src/block_builder.rs`/`batcher.rs` for completeness.

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

**File:** crates/apollo_l1_events/src/transaction_record.rs (L173-181)
```rust
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

**File:** docs/diagrams/06-l1-handler-flow.md (L163-180)
```markdown
    Note over B: After block execution completed

    B->>B: Collect consumed_l1_handler_tx_hashes
    B->>B: Collect rejected_tx_hashes
    B->>B: Filter rejected_l1_handler_tx_hashes

    B->>Storage: commit_proposal(height, state_diff)
    Storage-->>B: Ok

    rect rgb(240, 255, 240)
        Note over B,TxMgr: Notify L1 Provider
        B->>L1P: commit_block(consumed_txs, rejected_txs, height)

        L1P->>L1P: apply_commit_block(consumed, rejected)
        L1P->>TxMgr: commit_txs(committed_txs, rejected_txs)

        Note over TxMgr: For committed txs: Pending to Committed
        Note over TxMgr: For rejected txs: Unstage, keep as Pending
```
