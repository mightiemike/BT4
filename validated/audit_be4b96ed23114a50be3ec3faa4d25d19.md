### Title
Mempool admission has no fee-based eviction for gap-free accounts, allowing low-fee spam to permanently exhaust mempool capacity and block legitimate transactions - (File: crates/apollo_mempool/src/mempool.rs)

### Summary
The external report describes a buffer-draining Denial-of-Service where an attacker can repeatedly consume a scarce, shared resource (the LST adapter's withdrawal buffer) using cheap deposit/redeem cycles, denying legitimate users fair access because there is no priority/QoS mechanism governing access to that limited resource. The `apollo_mempool` crate has an analogous scarce, shared resource — `capacity_in_bytes` — and an analogous lack of fee-based QoS at admission time: eviction to make room for a new transaction is only possible against accounts that have a nonce gap, never against valid, fee-paying-but-low-fee transactions from gap-free accounts.

### Finding Description
When a new transaction would exceed the mempool's configured `capacity_in_bytes`, `add_tx_validations` calls `handle_capacity_overflow`, which in turn calls `try_make_space` to attempt eviction: [1](#0-0) .

`try_make_space` only evicts transactions belonging to accounts contained in `accounts_with_gap`, selected via `get_evictable_account`, which picks a random entry from that gap-only set: [2](#0-1) .

`handle_capacity_overflow` computes `required_space` and, if the incoming tx is not itself creating a gap, calls `try_make_space`; if `try_make_space` cannot free enough space (e.g., because no accounts currently have a gap), the transaction is rejected outright with `MempoolError::MempoolFull`, regardless of how much higher its tip/fee is than any transaction already occupying the pool: [3](#0-2) .

Crucially, the mempool's priority ordering by `tip`/`max_l2_gas_price` (`FeeTransactionQueue`'s `priority_queue`/`pending_queue`) is used only for selecting which transactions to include in a block via `pop_ready_chunk`/`iter_over_ready_txs`, not for deciding which transactions may occupy scarce pool capacity: [4](#0-3) [5](#0-4) . There is no mechanism to evict the globally lowest-fee, gap-free transaction to make room for an incoming higher-fee transaction — this is confirmed by the existing test suite, which explicitly documents that a full mempool with no evictable (gapped) accounts rejects any new transaction, even from a different, otherwise-valid account: [6](#0-5) .

This mirrors the reported bug class exactly: a shared, capacity-limited resource (buffer / mempool bytes) is allocated on a strict first-come basis with no fee/priority-based reallocation, so an unprivileged party can occupy it with minimal-value entries that are structurally immune to eviction (gap-free, single transaction per address, matching current nonce), permanently starving out legitimately higher-priority requests until the attacker's own transactions clear naturally.

### Impact Explanation
An attacker who controls, or cheaply creates, many distinct account addresses (e.g., low-cost deployed accounts, similar in spirit to the report's low-cost deposit/redeem cycling) can submit one transaction per address at each account's current nonce with the minimum viable fee. Since these transactions are gap-free, they are never inserted into `accounts_with_gap` and are therefore never eligible for eviction by `try_make_space`. Once `capacity_in_bytes` is saturated with such transactions, any subsequent transaction submission — including from legitimate, high-fee-paying users — is rejected with `MempoolError::MempoolFull` at the gateway/mempool boundary, irrespective of its fee. As blocks are produced and the attacker's transactions are consumed (freeing bytes), the attacker can resubmit fresh sybil transactions to keep the mempool continuously saturated, sustaining a rolling denial-of-service against transaction confirmation for the broader network. This matches the "network unable to confirm new transactions" impact class.

### Likelihood Explanation
The attack requires only the ability to submit ordinary, validly-formed transactions from multiple addresses (an unprivileged transaction sender capability), paying only the minimal required fee per transaction — no special privileges, no protocol-level trust assumptions, and no dependency on other nodes' misbehavior. The cost is proportional to `capacity_in_bytes` divided by the minimum transaction size, similar in economics to the original report's buffer-drain cost model, making this a realistically executable, moderate-cost DoS.

### Recommendation
Introduce fee/priority-aware admission control at mempool capacity limits so that a new transaction with sufficiently higher tip/fee than the lowest-priority occupant of the pool can trigger eviction of that lowest-priority transaction, not just eviction of gap-having accounts. Alternatively, reserve a portion of mempool capacity purely by priority (e.g., always allow admission of transactions with tip strictly greater than the minimum currently held), or increase minimum-fee admission thresholds dynamically as pool occupancy approaches capacity, similar to typical fee-market mempool designs, to prevent cheap gap-free spam from permanently displacing legitimate transactions.

### Proof of Concept
1. Configure/observe a mempool with `capacity_in_bytes` at its default or reduced value [7](#0-6) .
2. From N distinct sybil addresses, each with account_nonce = 0, submit one transaction each at tx_nonce = 0 with the minimum tip and `max_l2_gas_price` (gap-free, non-evictable), until `size_in_bytes() + tx.total_bytes() > capacity_in_bytes`, per `exceeds_capacity` [8](#0-7) .
3. Submit a legitimate transaction from a new address with a very high tip/fee. Because there is no account in `accounts_with_gap` to evict, `try_make_space` returns `false` and `handle_capacity_overflow` returns `Err(MempoolError::MempoolFull)` [9](#0-8) , exactly as exercised by the existing regression test `returns_error_when_no_evictable_accounts` [6](#0-5) .
4. As the attacker's low-fee transactions clear via block inclusion, repeat step 2 with fresh sybil addresses to keep the mempool saturated indefinitely, sustaining rejection of legitimate high-fee transactions.

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L433-435)
```rust
        if self.exceeds_capacity(tx, freed_bytes) {
            self.handle_capacity_overflow(tx, account_nonce, freed_bytes)?;
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L948-956)
```rust
    // Returns true if adding the given transaction would exceed the mempool capacity, after
    // crediting `freed_bytes` that an accompanying removal (e.g. a fee-escalation replacement)
    // will free. `freed_bytes` is 0 when there is no such removal.
    fn exceeds_capacity(&self, tx: &InternalRpcTransaction, freed_bytes: u64) -> bool {
        // The to-be-removed transaction is still counted in `size_in_bytes()` here, so subtract
        // what its removal frees. `saturating_sub` guards the (impossible) underflow defensively.
        (self.size_in_bytes() + tx.total_bytes()).saturating_sub(freed_bytes)
            > self.config.static_config.capacity_in_bytes
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L992-1039)
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

**File:** crates/apollo_mempool/src/fee_transaction_queue.rs (L22-30)
```rust
pub struct FeeTransactionQueue {
    gas_price_threshold: GasPrice,
    // Transactions with gas price above gas price threshold (sorted by tip).
    priority_queue: BTreeSet<PriorityTransaction>,
    // Transactions with gas price below gas price threshold (sorted by price).
    pending_queue: BTreeSet<PendingTransaction>,
    // Set of account addresses for efficient existence checks.
    address_to_tx: HashMap<ContractAddress, TransactionReference>,
}
```

**File:** crates/apollo_mempool/src/fee_transaction_queue.rs (L59-67)
```rust
    fn pop_ready_chunk(&mut self, n_txs: usize) -> Vec<TransactionReference> {
        let txs: Vec<TransactionReference> =
            (0..n_txs).filter_map(|_| self.priority_queue.pop_last().map(|tx| tx.0)).collect();
        for tx in &txs {
            self.address_to_tx.remove(&tx.address);
        }

        txs
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

**File:** crates/apollo_mempool_config/src/config.rs (L85-99)
```rust
impl Default for MempoolStaticConfig {
    fn default() -> Self {
        Self {
            enable_fee_escalation: true,
            validate_resource_bounds: true,
            fee_escalation_percentage: 10,
            declare_delay: Duration::from_secs(1),
            committed_nonce_retention_block_count: 100,
            capacity_in_bytes: 1 << 30, // 1GB.
            behavior_mode: BehaviorMode::Starknet,
            recorder_url: "https://recorder_url"
                .parse::<Url>()
                .expect("recorder_url must be a valid Recorder URL"),
        }
    }
```
