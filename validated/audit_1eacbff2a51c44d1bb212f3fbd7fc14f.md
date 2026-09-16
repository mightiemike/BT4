### Title
Unbounded, unrationed L1-handler proposable queue lets a cheap message-spam attacker indefinitely delay genuine L1→L2 messages - (File: crates/apollo_l1_events/src/transaction_manager.rs)

### Summary
The `TransactionManager` that feeds L1-handler transactions into block proposals selects transactions strictly in FIFO order of L1 scrape timestamp, capped at a small `max_l1_handler_txs_per_block_proposal` per block, with no per-sender limit or fairness mechanism. This is structurally the same bug class as the reported VUSD withdrawal-queue DoS: an attacker who can cheaply enqueue many small entries at the head of a strictly-ordered, throughput-capped queue can indefinitely delay legitimate entries queued behind them.

### Finding Description
`TransactionManager::get_txs` selects the *oldest* pending, cooldown-passed, unstaged transactions from `proposable_index` (a `BTreeMap<UnixTimestamp, Vec<TransactionHash>>` ordered by scrape timestamp / arrival order), up to `n_txs`: [1](#0-0) 

There is no mechanism limiting how many L1 handler transactions a single L1 sender/contract can inject into this index, and no priority/fairness scheme beyond arrival order — directly mirroring the `withdrawals` array in the VUSD report, which is also a strict FIFO with a per-processing cap (`maxWithdrawalProcesses`) and only a trivial per-entry minimum.

On the batcher side, only a small, fixed number of L1 handler transactions is pulled per block proposal: [2](#0-1) 

and `ProposeTransactionProvider` fetches L1 handler transactions from the front of this same ordered structure before switching to mempool transactions: [3](#0-2) 

Since `L1HandlerTransaction`s are admitted purely by arrival order at scrape time (`add_tx` simply records+indexes new messages by timestamp, with no sender-based throttling), an attacker who can repeatedly trigger cheap `sendMessageToL2`-style L1 messages targeting any L2 contract can occupy the front of `proposable_index` faster than the fixed per-block cap can drain it, pushing genuine users' L1-originated transactions (e.g., bridge deposits, cross-layer calls) arbitrarily far back in the queue.

### Impact Explanation
As long as the attacker sustains message injection at a rate exceeding `max_l1_handler_txs_per_block_proposal` transactions per block, every subsequently-arriving legitimate L1 handler transaction is delayed behind the attacker's backlog indefinitely (the delay grows without bound as the attack continues), denying timely finalization of legitimate cross-layer operations such as deposits/bridge messages. This matches the accepted severity class of the referenced report: a queue-ordering/rationing defect that lets a low-cost griefer indefinitely stall other users' pending operations.

### Likelihood Explanation
The action is reachable by any unprivileged L1 message sender — the exact "L1 message" path called out in scope. The only cost to the attacker is L1 gas plus the (attacker-controlled, arbitrarily small) L2 fee attached to a self-triggered message, analogous to the trivial 5-VUSD minimum in the original report that "is not enough to prevent DOS." No additional privilege, timing, or race condition is required; the FIFO/no-rationing design guarantees the effect is deterministic and reproducible on every affected node identically (thus not merely a griefing-of-self issue but a network-wide processing-order property enforced by the Starknet OS/consensus rules).

### Recommendation
Introduce fairness/rationing when selecting proposable L1 handler transactions, e.g.:
- Round-robin or per-L1-sender interleaving instead of pure global FIFO by scrape timestamp, so no single origin can monopolize the fixed per-block L1-handler slot budget.
- A configurable per-sender/per-contract cap on outstanding pending L1 handler transactions considered for a given proposal window.
- Increasing `max_l1_handler_txs_per_block_proposal` adaptively when a backlog from a single source is detected, or de-prioritizing repeat low-value senders.

### Proof of Concept
1. Deploy an L1 contract (or an EOA loop) that repeatedly calls the Starknet core contract's L1→L2 messaging entrypoint, each with minimal payload/fee, targeting any L2 contract, at a rate greater than `max_l1_handler_txs_per_block_proposal` (default 3) transactions per L2 block.
2. Each message is scraped and inserted into `TransactionManager::proposable_index` keyed by scrape timestamp via `add_tx`.
3. A legitimate user's genuine L1→L2 message (e.g., a bridge deposit) is scraped after the attacker has already queued a large backlog.
4. `TransactionManager::get_txs` (crates/apollo_l1_events/src/transaction_manager.rs:72-114) always drains the oldest unstaged entries first; as long as the attacker keeps producing more entries every block than the fixed per-block cap can process, the honest user's transaction never reaches the front of the index and is delayed indefinitely.

### Citations

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L72-86)
```rust
    pub fn get_txs(&mut self, n_txs: usize, now: u64) -> Vec<L1HandlerTransaction> {
        // Oldest        Now.sub(timelock)     Newest       Now
        //  |<---  passed  --->|                 |           |
        //  |<--- cooldown --->|                 |           |
        // t-------------------------------------------------->
        let cutoff = now.saturating_sub(self.config.l1_handler_proposal_cooldown_seconds.as_secs());
        let past_cooldown_txs = self.proposable_index.range(..cutoff);

        // Linear scan, but we expect this to be a small number of transactions (< 10 roughly).
        let unstaged_tx_hashes: Vec<_> = past_cooldown_txs
            .flat_map(|(_timestamp, tx_hashes)| tx_hashes.iter())
            .skip_while(|&&tx_hash| self.is_staged(tx_hash))
            .take(n_txs)
            .copied()
            .collect();
```

**File:** crates/apollo_node/resources/config_schema.json (L327-331)
```json
  "batcher_config.static_config.max_l1_handler_txs_per_block_proposal": {
    "description": "The maximum number of L1 handler transactions to include in a block proposal.",
    "privacy": "Public",
    "value": 3
  },
```

**File:** crates/apollo_batcher/src/transaction_provider.rs (L94-110)
```rust
    async fn get_l1_handler_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .l1_events_provider_client
            .get_txs(n_txs, self.height)
            .await
            .inspect_err(|err| {
                warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            })
            .unwrap_or_default()
            .into_iter()
            .map(InternalConsensusTransaction::L1Handler)
            .collect())
    }
```
