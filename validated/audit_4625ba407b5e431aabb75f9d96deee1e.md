### Title
Mempool admission has no fee-based preemption at capacity, letting low-fee spam from disposable accounts permanently deny entry to legitimate higher-fee transactions - (File: `crates/apollo_mempool/src/mempool.rs`)

### Summary
The mempool's capacity-overflow handling (`handle_capacity_overflow` / `try_make_space`) only ever evicts transactions belonging to accounts that currently have a nonce gap (`accounts_with_gap`). It never evicts existing pool transactions purely because an incoming transaction offers a higher tip/fee. An attacker can fill the mempool to `capacity_in_bytes` with minimum-fee, nonce-sequential (gap-free) transactions submitted from many disposable addresses. Once full, any new transaction — including one with a very high tip from a legitimate user — is rejected with `MempoolError::MempoolFull` because there are no gapped accounts to evict, mirroring the reported "max out a fixed capacity with low-value items" bug class from the NFT-deposit report.

### Finding Description
`add_tx_validations` calls `exceeds_capacity` and, if the mempool would overflow, `handle_capacity_overflow`: [1](#0-0) 

`handle_capacity_overflow` only attempts eviction via `try_make_space` when the incoming tx is not itself creating a gap, and `try_make_space` exclusively selects victims from `self.accounts_with_gap`: [2](#0-1) 

`get_evictable_account` picks a random account only from the gap set, with no fallback to "lowest fee/tip transaction in the pool" when no gapped account exists: [3](#0-2) 

The only other path to remove an existing pool member is fee-escalation replacement (`validate_fee_escalation` / `remove_replaced_tx`), which strictly applies when the *same account* replaces its own pending transaction at the *same nonce* with a higher fee — it does nothing for a *different* account trying to enter a full pool. The dedicated tests confirm this design: `returns_error_when_no_evictable_accounts` and `add_tx_exceeds_capacity` explicitly show `MempoolError::MempoolFull` is returned once capacity is reached and there are no gapped accounts, regardless of the new transaction's fee: [4](#0-3) 

Because `capacity_in_bytes` is a single, protocol-wide (not per-account) limit, and each attacker-controlled address only needs one small, valid, gap-free transaction to occupy space and to never become "evictable," a set of throwaway accounts submitting minimum-tip transactions can permanently saturate the mempool. This is directly analogous to the reported NFT bug: a shared, hard-capped resource slot pool can be monopolized with minimum-value entries, permanently blocking a legitimate high-value entrant from being admitted at all — not merely deprioritized.

### Impact Explanation
While the block-building priority queue (`FeeTransactionQueue::pop_ready_chunk`) does order execution by tip once transactions are admitted, the vulnerability is at the admission boundary: a legitimate, high-tip transaction can never enter the mempool while it is saturated with attacker-controlled low-fee, gap-free transactions, since no fee-based eviction exists to make room. This can render the sequencer's mempool unable to accept and eventually confirm new (especially higher-value/urgent) transactions from honest users as long as the attacker keeps refreshing their gap-free occupancy (e.g., submitting a new low-fee nonce-0 tx from a fresh address whenever one of their transactions is finally included), which is inexpensive because minimum-fee transactions are cheap to produce and each only needs to occupy space, not get executed.

### Likelihood Explanation
Any unprivileged transaction sender can reach this path: it requires only submitting valid, low-fee, nonce-correct (gap-free) transactions from a set of addresses via the normal gateway → mempool `add_tx` flow. No special privilege, timing, or protocol knowledge beyond the mempool's public capacity and fee model is needed, and the config default capacity (`1_073_741_824` bytes, i.e., 1 GiB) is a fixed, known target size that could feasibly be exhausted by a moderately funded campaign of many small, low-fee transactions.

### Recommendation
Add a fee/tip-aware eviction fallback to `handle_capacity_overflow`/`try_make_space` used when there are no (or insufficient) gapped accounts to evict: when a legitimately higher-priority incoming transaction cannot fit, allow evicting the pool's currently lowest-tip/lowest-fee gap-free transaction(s) (e.g., drawn from `pending_queue`/tail of `priority_queue`) provided the incoming transaction's fee strictly exceeds theirs by some margin, similar in spirit to the existing same-account fee-escalation replacement, but generalized across accounts. Alternatively, reserve a portion of mempool capacity that can only be filled by transactions above a certain fee percentile, or introduce a minimum fee floor for admission when the pool is at or near capacity.

### Proof of Concept
1. Configure a mempool with `capacity_in_bytes` set to its default (or any bound).
2. Submit `N` distinct transactions from `N` unique, valid, gap-free addresses (nonce 0 tx per address, each with the protocol's minimum allowed tip/fee), sized to fill `capacity_in_bytes` exactly (as done in the test `add_tx_exceeds_capacity`, `crates/apollo_mempool/src/fee_mempool_test.rs:642-687`).
3. Submit one more, high-tip, gap-free transaction from a fresh legitimate address.
4. Observe `add_tx_expect_error(&mut mempool, &input_tx, MempoolError::MempoolFull)` — the high-tip transaction is rejected outright, exactly as shown in `returns_error_when_no_evictable_accounts` (`crates/apollo_mempool/src/fee_mempool_test.rs:1796-1816`) — confirming there is no fee-based path to admit it despite ample fee headroom over the occupying transactions.

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L992-1040)
```rust
    pub fn get_evictable_account(&self) -> Option<ContractAddress> {
        let len = self.accounts_with_gap.len();
        if len == 0 {
            return None;
        }
        let random_index = thread_rng().gen_range(0..len);
        self.accounts_with_gap.get_index(random_index).copied()
    }

    // Attempts to make space for a new transaction by evicting existing transactions.
    // Returns true if enough space was freed, false otherwise.
    pub fn try_make_space(&mut self, required_space: u64) -> bool {
        let mut total_space_freed = 0;
        let mut evicted_txs = Vec::new();

        while total_space_freed < required_space {
            let Some(address) = self.get_evictable_account() else {
                break;
            };

            let txs: Vec<_> = self.tx_pool.account_txs_sorted_by_nonce(address).copied().collect();
            for tx_ref in txs.iter().rev() {
                let tx = self
                    .tx_pool
                    .remove(tx_ref.tx_hash)
                    .expect("Transaction must exist in the pool.");
                total_space_freed += tx.total_bytes();
                evicted_txs.push(*tx_ref);
                metric_count_evicted_txs(1);
                self.decrement_stuck_txs_if_gap_account(address, 1);
                if total_space_freed >= required_space {
                    break;
                }
            }

            // Clean up if account is now empty.
            if !self.tx_pool.contains_account(address) {
                self.accounts_with_gap.swap_remove(&address);
            }
        }

        // Keep the queue consistent with the pool: the evicted txs were removed from the pool, so
        // drop their (now-orphaned) queue references too. In fee mode gap accounts are never
        // queued, so this is a no-op there; in Echonet/FIFO mode they are, and skipping this leaves
        // a dangling reference that panics the next `get_txs`.
        self.tx_queue.remove_txs(&evicted_txs);

        total_space_freed >= required_space
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L1042-1063)
```rust
    fn handle_capacity_overflow(
        &mut self,
        tx: &InternalRpcTransaction,
        account_nonce: Nonce,
        freed_bytes: u64,
    ) -> Result<(), MempoolError> {
        let address = tx.contract_address();

        let account_has_gap = self.accounts_with_gap.contains(&address);
        let account_has_txs = self.tx_pool.contains_account(address);
        let closing_gap = tx.nonce() == account_nonce;
        let creating_gap = (account_has_gap || !account_has_txs) && !closing_gap;

        // Only the net growth must be evicted: an accompanying replacement removal frees
        // `freed_bytes` (0 when there is no replacement).
        let required_space = tx.total_bytes().saturating_sub(freed_bytes);
        if !creating_gap && self.try_make_space(required_space) {
            return Ok(());
        }

        Err(MempoolError::MempoolFull)
    }
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L1796-1816)
```rust
#[rstest]
fn returns_error_when_no_evictable_accounts() {
    let not_evictable_tx = add_tx_input!(tx_hash: 1, address: "0x0", tx_nonce: 0, account_nonce: 0);

    let mut mempool = Mempool::new(
        MempoolConfig {
            static_config: MempoolStaticConfig {
                capacity_in_bytes: not_evictable_tx.tx.total_bytes(),
                ..Default::default()
            },
            ..Default::default()
        },
        Arc::new(FakeClock::default()),
    );

    add_tx(&mut mempool, &not_evictable_tx);
    assert!(mempool.accounts_with_gap().is_empty());

    let trigger_tx = add_tx_input!(tx_hash: 2, address: "0x1", tx_nonce: 0, account_nonce: 0);
    add_tx_expect_error(&mut mempool, &trigger_tx, MempoolError::MempoolFull);
}
```
