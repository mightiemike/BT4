Confirmed: the `L1EventsProvider::validate` and `get_txs` paths call `self.tx_manager.validate_tx(tx_hash, self.clock.unix_now())` / `get_txs(n_txs, self.clock.unix_now())`, which pass a local wall-clock (`Clock::unix_now()`, i.e., `DefaultClock` reading real system time) into `TransactionRecord::update_time_based_state`, rather than a value derived from the block being built/validated (e.g., the proposal's `timestamp` field agreed upon in `ProposalInit`). This is the exact analog of the report's root cause. [1](#0-0) [2](#0-1) 

### Title
Non-deterministic wall-clock timestamp used for L1 handler cancellation-timelock state transition causes honest-node divergence - (File: crates/apollo_l1_events/src/transaction_record.rs)

### Summary
The Arcade report's root cause is a bright-line `block.timestamp >= dueDate` check with no grace period, allowing an actor to act unilaterally the instant a boundary is crossed, and to do so unpredictably relative to another actor's own action because the check isn't tied to a value both parties observe identically. The sequencer's L1 handler cancellation-timelock logic has the analogous defect: the boundary check `unix_now >= requested_at + cancellation_timelock` in `TransactionRecord::update_time_based_state` is driven by each node's own local wall-clock time (`Clock::unix_now()`), not by a value derived from the block content that all nodes agree on.

### Finding Description
`L1EventsProvider::validate` and `L1EventsProvider::get_txs` fetch `self.clock.unix_now()` (backed by `DefaultClock`, real system time) and feed it into `TransactionManager::validate_tx` / `get_txs`, which in turn call `TransactionRecord::update_time_based_state(unix_now, policy)`. That function transitions a transaction's state from `CancellationStartedOnL2` to `CancelledOnL2` the instant `unix_now >= requested_at.saturating_add(cancellation_timelock)`, exactly mirroring the `dueDate` check pattern in the Arcade bug (`if dueDate >= block.timestamp) revert ...`), with no grace period or deterministic anchor. [3](#0-2) [2](#0-1) 

Because `unix_now` is each node's local clock rather than a block-derived timestamp (unlike the deterministic `ProposalInit.timestamp` window check used elsewhere for block timestamp validity, e.g. `is_proposal_init_valid`), a proposer and a validator racing near the timelock boundary can reach different conclusions about whether a given L1-handler transaction is `Validated` or `Invalid(CancelledOnL2)` for the same logical instant, purely due to clock skew/latency between nodes — there is no consensus-anchored point in time at which all honest nodes are guaranteed to agree. [4](#0-3) 

### Impact Explanation
If a proposer includes (or excludes) an L1-handler transaction based on its own clock reading being just before/after the cancellation timelock boundary, while validators (with slightly different clocks or processing at a slightly later wall-clock instant) compute the opposite state, validation of an otherwise-valid proposal can fail non-deterministically across honest nodes, or a transaction can be inconsistently treated as cancelled vs. still pending between the propose and validate phases of the same round. This falls into the "honest-node divergence" and "network unable to confirm new transactions" categories, since repeated disagreement at the boundary can stall or reject proposals that would otherwise be valid, and — more critically — inconsistent finalization of "cancelled" state for an L1 message could permanently freeze/mis-drop a user's bridged transaction if a node commits to a wrong assessment of cancellation.

### Likelihood Explanation
This requires the wall-clock time on different nodes (or the time between when a proposer builds a block and a validator validates it) to straddle the exact cancellation-timelock boundary, a naturally occurring race any time a cancellation is requested near real time — no attacker action beyond a normal L1 message sender requesting a cancellation is needed to create the race window, making this a plausible, environment-triggered condition rather than a contrived one, though it only manifests within a narrow timing window each time.

### Recommendation
Anchor `update_time_based_state`'s expiry check to a deterministic, consensus-agreed value (e.g., the block's `timestamp`/`BlockTimestamp` being proposed or validated) rather than each node's local wall-clock `unix_now()`, and/or introduce a grace margin around the boundary so that both proposer and validator agree on validity near the threshold, consistent with how `is_proposal_init_valid` already treats block timestamps with an explicit window rather than raw system time.

### Proof of Concept
1. Node A (proposer) calls `get_txs` at wall-clock time `t0 = requested_at + cancellation_timelock - 1` (still validatable) and includes L1-handler tx `X`, using `self.clock.unix_now()`. [5](#0-4) 
2. Node B (validator), whose local clock runs slightly ahead or which processes the proposal moments later, calls `validate(tx_hash, height)` at wall-clock `t1 = requested_at + cancellation_timelock` or later. [3](#0-2) 
3. `TransactionRecord::update_time_based_state` on Node B computes `unix_now >= requested_at + cancellation_timelock == true` and transitions `X` to `CancelledOnL2`, causing `validate_tx` to return `Invalid(CancelledOnL2)` for a transaction Node A legitimately proposed as `Validated`. [6](#0-5) 
4. This produces disagreement between honest nodes on the validity of the same proposal, purely from unsynchronized local clocks straddling the hard-coded boundary, with no deterministic tie-breaker.

### Citations

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L213-246)
```rust
    /// Retrieves up to `n_txs` transactions that have yet to be proposed or accepted on L2.
    /// Used to make new proposals. Must be in Propose state.
    #[instrument(skip(self), err)]
    pub fn get_txs(
        &mut self,
        n_txs: usize,
        height: BlockNumber,
    ) -> L1EventsProviderResult<Vec<L1HandlerTransaction>> {
        if self.state.is_uninitialized() {
            return Err(L1EventsProviderError::Uninitialized);
        }

        self.check_height_with_error(height)?;

        match self.state {
            ProviderState::Propose => {
                let txs = self.tx_manager.get_txs(n_txs, self.clock.unix_now());
                info!(
                    "Returned {} out of {} transactions, ready for sequencing.",
                    txs.len(),
                    n_txs
                );
                debug!(
                    "Returned L1Handler txs: {:?}",
                    txs.iter()
                        .map(|tx| format!(
                            "L2 tx hash: {}, L1-L2 msg hash: {}",
                            tx.tx_hash,
                            tx.tx.calc_msg_hash()
                        ))
                        .collect::<Vec<_>>()
                );
                Ok(txs)
            }
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L257-277)
```rust
    #[instrument(skip(self), err)]
    pub fn validate(
        &mut self,
        tx_hash: TransactionHash,
        height: BlockNumber,
    ) -> L1EventsProviderResult<ValidationStatus> {
        if self.state.is_uninitialized() {
            return Err(L1EventsProviderError::Uninitialized);
        }

        self.check_height_with_error(height)?;
        match self.state {
            ProviderState::Validate => {
                Ok(self.tx_manager.validate_tx(tx_hash, self.clock.unix_now()))
            }
            _ => Err(L1EventsProviderError::UnexpectedProviderState {
                expected: ProviderState::Validate,
                found: self.state,
            }),
        }
    }
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L194-208)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L259-284)
```rust
    let now: u64 = clock.unix_now();
    let last_block_timestamp =
        proposal_init_validation.previous_proposal_init.as_ref().map_or(0, |info| info.timestamp);
    if init_proposed.timestamp < last_block_timestamp {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is too old: last_block_timestamp={}, proposed={}",
                last_block_timestamp, init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.timestamp > now + proposal_init_validation.block_timestamp_window_seconds {
        return Err(ValidateProposalError::InvalidProposalInit(
            init_proposed.clone(),
            proposal_init_validation.clone(),
            format!(
                "Timestamp is in the future: now={}, block_timestamp_window_seconds={}, \
                 proposed={}",
                now,
                proposal_init_validation.block_timestamp_window_seconds,
                init_proposed.timestamp
            ),
        ));
    }
```
