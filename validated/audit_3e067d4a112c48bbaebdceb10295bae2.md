### Title
L1 Handler Transaction Records Can Be Deleted While Staged In An In-Flight Proposal, Causing Honest-Node Validation Divergence - (File: crates/apollo_l1_events/src/transaction_manager.rs)

### Summary
`TransactionManager::finalize_cancellation` unconditionally deletes an L1 handler transaction's record — even if that transaction is currently staged inside an in-progress block proposal — the moment an L1 cancellation-finalization event is scraped. Other validating nodes that receive the proposal but have not yet (or have already) processed the same L1 event will get inconsistent `validate_tx` results for the identical transaction hash, mirroring the reported class of bug: a resource's authoritative "still active / reserved" state is cleared out-of-band while it is still referenced by an unfinished workflow (the outstanding block proposal), and a different check downstream fails to recognize that in-flight reservation.

### Finding Description
`TransactionManager` tracks each L1 handler transaction via a `TransactionRecord` with a `state` field and a `staged_epoch` used to mark a tx as "used in the current proposal attempt" [1](#0-0) .

When a block is proposed, `get_txs` selects unstaged, cooldown-passed, `Pending` transactions and calls `try_mark_staged`, tagging them so they are not proposed twice within the same block attempt [2](#0-1) . Validating nodes that receive this proposal call `validate_tx` for each transaction hash, which looks up the record and, if found and `is_validatable()`, marks it staged and returns `Validated`; if the record is missing, it falls back to `NotFound` [3](#0-2) .

Separately, `finalize_cancellation` is invoked when the L1 events scraper observes that a transaction's cancellation was finalized on L1. The method's own comment acknowledges the danger and proceeds anyway:

"Regardless of the state of the tx in the record, if we get the cancellation event from the L1 contract, we delete this tx from the records and from the proposable index, even if it was Pending and ready to be proposed (which is not supposed to happen, hence the warning)." [4](#0-3) 

Note that `finalize_cancellation` performs no check against `staged_epoch`/`current_staging_epoch` before calling `self.records.remove(&tx_hash)` — it does not treat a "staged" (i.e., already included in an outstanding, unfinalized proposal) transaction any differently from a truly idle one. This is directly analogous to the Flayer bug: `unlockProtectedListing` clears the listing owner (`delete _protectedListings[...]`) while leaving the asset in an intermediate "reserved for withdrawal" state (`canWithdrawAsset[...] = msg.sender`), and `Locker::isListing` — a downstream check used by unrelated flows (`redeem`) — fails to recognize this in-between state as still occupied, letting another actor claim the asset. Here, the record deletion in `finalize_cancellation` is the equivalent of clearing the "owner" field, and the different validating nodes' `validate_tx` calls are the equivalent of `isListing`/`redeem`: they inconsistently perceive the same in-flight resource (the staged L1 handler tx) as either present (`Validated`) or gone (`NotFound`), depending purely on the local, non-deterministic timing of L1 event scraping relative to proposal receipt.

### Impact Explanation
Because L1 event scraping timing is inherently non-deterministic across independent sequencer nodes, a race between (a) a proposer staging an L1 handler tx into a block proposal and (b) any validator's local scraper observing the tx's L1 cancellation finalization event causes different honest validators to reach different `ValidationStatus` outcomes (`Validated` on nodes that haven't yet scraped the event vs. `NotFound` on nodes that have) for the exact same proposed block. This is an honest-node divergence: validators applying identical protocol logic to the identical proposal reach different conclusions about its validity, which can repeatedly stall consensus rounds for a given height whenever this race window is hit, since the proposal will be rejected by a subset of validators purely due to un-synchronized local bookkeeping rather than a genuine protocol violation.

### Likelihood Explanation
The race window requires an L1 handler transaction to be both (1) actively staged in a block proposal by the current sequencer, and (2) have its cancellation finalized and scraped by the L1 events pipeline at roughly the same time. This is reachable purely from the L1 message sender's side (requesting and then finalizing a cancellation on the core contract is a normal, permissionless L1 action for the message's sender) combined with normal sequencer operation — no malicious operator, peer, or privileged actor is required, only ordinary timing between L1 scraping and L2 proposal cadence.

### Recommendation
In `finalize_cancellation`, check whether the transaction is currently staged (`is_staged(current_staging_epoch)`) before deleting its record. If staged, defer the deletion (e.g., mark it for removal and only physically remove it once `rollback_staging`/`commit_txs` clears the staging epoch for that block attempt), so that all validators observe a consistent, non-racy state for a transaction referenced by an outstanding proposal, analogous to preserving the Flayer listing as "active" until fully finalized rather than treating it as gone the instant an intermediate step occurs.

### Proof of Concept
1. Node P (proposer) has L1 handler tx `X` in `Pending` state in its `proposable_index`.
2. P calls `get_txs`, which stages `X` (`try_mark_staged`) and includes it in proposal for block `N` [2](#0-1) ; P broadcasts the proposal.
3. Concurrently, the L1 message sender's cancellation for `X` (requested earlier) finalizes on L1; node V1 (validator) has already scraped this finalize event and calls `finalize_cancellation(X)`, which deletes `X`'s record from V1's `TransactionManager` regardless of the fact that `X` is embedded in the just-received proposal [4](#0-3) .
4. V1 validates the proposal by calling `validate_tx(X, ...)`; since the record no longer exists, this returns `InvalidValidationStatus::NotFound` [3](#0-2) , causing V1 to reject block `N`.
5. Node V2, whose scraper has not yet processed the finalize event, still has `X`'s record as `Pending`; `validate_tx(X, ...)` returns `Validated`, and V2 accepts block `N`.
6. P and V2 vote to accept the block while V1 rejects it — an honest-node divergence on the same proposal, purely due to un-synchronized handling of a record deletion versus an in-flight staged reference, matching the reported bug class of "resource cleared/removed while still logically reserved by an unfinished operation, causing inconsistent downstream recognition."

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L41-48)
```rust
    /// Generation counter used to prevent double usage of an l1 handler transaction in a single
    /// block.
    /// Calling `get_txs` or `validate_tx` tags the touched transactions with the current block
    /// counter, so that further calls will know not to touch them again.
    /// At the start and end (commit) of every block, the counter is incremented, thus "unstaging"
    /// all tagged transactions from the previous block attempt.
    // TODO(Gilad): remove "for rejected" from name when uncommitted is migrated to records DS.
    current_staging_epoch: StagingEpoch,
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
