## Title
`TransactionManager::get_txs` can panic on an internal consistency assertion when the "staged transactions form a prefix" invariant is violated — ([File: crates/apollo_l1_events/src/transaction_manager.rs])

### Summary
The external report describes `DnGmxBatchingManager.executeBatchDeposit()` reverting because a nested call re-enters a state-gated function (`depositToken`) whose guard (the cooldown check) assumes it is only reachable from a single top-level context. The Starknet sequencer analog is `TransactionManager::get_txs`, which similarly assumes an invariant that is only "usually" true — that already-staged L1-handler transactions form a *contiguous prefix* of the cooldown-filtered, timestamp-ordered candidate list — and uses `skip_while` instead of `filter` to skip them. If that invariant is violated, the function proceeds to call `try_mark_staged` on an already-staged transaction, which returns `false`, and the subsequent `assert_eq!(newly_staged, Some(true), ...)` panics.

### Finding Description
`get_txs` builds its candidate list from `proposable_index` (a `BTreeMap<UnixTimestamp, Vec<TransactionHash>>`), and relies on the documented invariant: [1](#0-0) 

filtering already-staged entries with `skip_while`, not `filter`: [2](#0-1) 

`skip_while` only drops a *leading run* of elements matching the predicate; as soon as one unstaged element is encountered it stops skipping and includes everything after it, staged or not. The code then unconditionally asserts each selected hash gets newly staged: [3](#0-2) 

`try_mark_staged` fails (returns `false`) for an already-staged record, which is exactly the outcome the `assert_eq!` treats as an unrecoverable invariant violation ("Inconsistent storage state"), causing a `panic!`.

The "staged is a prefix" invariant depends on strictly ordered insertion into `proposable_index` by `scrape_timestamp`, and on `get_txs`/`validate_tx` always staging from the front. `maintain_indices` inserts new transactions keyed by `scrape_timestamp` under an explicit, acknowledged assumption: [4](#0-3) 

and the code itself carries an open TODO admitting `scrape_timestamp` (wall-clock scrape time) rather than the L1 event's `created_at_block_timestamp` is used for ordering: [5](#0-4) 

Additionally, `validate_tx` (used by a validator node) can mark an arbitrary transaction hash as staged based on the order transactions arrive from a proposal, not necessarily the oldest-first order that `get_txs` assumes: [6](#0-5) 

If a node transitions from validating a proposal (staging a non-prefix subset via `validate_tx`) back into a role where `get_txs` is invoked before `rollback_staging`/`start_block` resets the epoch, or if any interleaving of `add_tx`/`validate_tx`/`get_txs` produces a staged transaction that is not the earliest unstaged one in timestamp order, the very next `get_txs` call hits the `skip_while` gap and panics on the assertion — exactly analogous to the Sherlock finding where a state-gate assumption (single-entry cooldown) is violated by a reachable nested/alternate call path.

### Impact Explanation
A `panic!` inside `TransactionManager::get_txs`, reached via `L1EventsProvider::get_txs` from the batcher's propose flow, aborts the block-building thread/task for that sequencer node. Since this is invoked on every Propose session when L1-handler transactions are pending, a triggered inconsistency reliably prevents that node from producing valid proposals going forward (the invariant violation, once introduced into `proposable_index`, persists across blocks since staging state resets only per-block while the ordering defect in the map itself does not self-heal). This can degrade block production availability for the affected sequencer — a liveness/DoS impact on an unprivileged, protocol-reachable code path (transaction submission via L1 message → L1-handler ingestion → block proposal), not requiring any privileged/malicious operator action.

### Likelihood Explanation
Likelihood is moderate: the bug requires a genuine gap between "already staged" and "still assumed to be an ordered prefix," which the code's own comments concede depends on `scrape_timestamp` ordering (not the more correct `created_at_block_timestamp`) and on cross-role state reuse (`validate_tx` staging order vs. `get_txs` prefix assumption) that is not proven safe anywhere in this function. It is not a common-case failure under simple, well-behaved traffic, but it is reachable without any malicious operator/proposer/peer — purely through ordinary L1 message delivery timing and normal proposer/validator role transitions across heights, which are exactly the kinds of "certain conditions" flagged in the original disclosure.

### Recommendation
Replace `skip_while(is_staged)` with `filter(|hash| !is_staged(hash))` in `get_txs` so staged transactions are excluded regardless of position, removing the fragile prefix invariant entirely. Additionally, either strengthen `try_mark_staged`'s call site to tolerate an already-staged hash (skip rather than assert-panic) or make the "prefix" invariant an enforced/tested property (e.g., derive ordering from `created_at_block_timestamp` per the existing TODO, and assert-and-recover rather than panic) so a legitimate timing/ordering edge case cannot crash block production.

### Proof of Concept
Not independently reproduced due to the read-only nature of this analysis (no test harness executed). Conceptually:
1. Node acts as validator for height N, receiving L1-handler tx hashes `[B, A]` in a proposal where `A` has an earlier `scrape_timestamp` than `B` but arrives second; `validate_tx(B)` then `validate_tx(A)` stages `B` first, breaking the assumed prefix order in `proposable_index` (`A` is earlier in the BTreeMap ordering but unstaged after `B` is staged).
2. Block validation completes; `commit_block`/`rollback_staging` resets `current_staging_epoch`, but this does not fix the map ordering issue for any subsequent similar timing pattern in the same or later block.
3. If the node instead needed to call `get_txs` while `B` (or another non-leading element) remains staged from an incomplete/aborted flow before `start_block` resets staging, `skip_while(is_staged)` stops skipping at the first unstaged item that happens to sit ahead of `B` in iteration order, then `take(n_txs)` includes `B` again, and `try_mark_staged(B)` returns `false`, triggering the `assert_eq!(..., Some(true), "Inconsistent storage state...")` panic in [7](#0-6) .

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L35-39)
```rust
    /// Ordered lexicographically by scraping moment timestamp, then order-of-arrival for
    /// identical timestamps, also at any point the staged transactions are a prefix of the
    /// structure under this order.
    /// Invariant: contains all hashes of transactions that are proposable, and only them.
    /// Invarariant 2: Once removed from this index, a transaction will never be proposed again.
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L71-71)
```rust
    // TODO(Arni): use created_at_block_timestamp in addition to scrape_timestamp.
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L81-97)
```rust
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L99-113)
```rust
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
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L116-145)
```rust
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

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L376-392)
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
```
