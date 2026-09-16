### Title
Order-Dependent Gap Detection Allows Permanent Mempool Filling with Unevictable "Stuck" Transactions (Denial of Service) - (File: crates/apollo_mempool/src/mempool.rs)

### Summary
The mempool's capacity-overflow handling relies on `update_accounts_with_gap` to decide which accounts are eligible for eviction when the mempool is full. The gap-detection logic treats an account as **not** having a gap whenever a delayed declare transaction happens to occupy the account's current nonce, even though the account may hold many higher-nonce transactions that are just as "stuck" (non-executable) as a genuine nonce gap. This mirrors the OpenQ report's root cause: a resource-limit check (`TOKEN_ADDRESS_LIMIT` / here, mempool `capacity_in_bytes`) whose enforcement is order- and state-dependent, allowing an attacker to consume/occupy a scarce, capacity-limited resource in a way the eviction mechanism cannot reclaim, while legitimate senders are rejected once the limit is hit.

### Finding Description
`exceeds_capacity` measures usage against `capacity_in_bytes` across both the transaction pool and delayed declares: [1](#0-0) 

When capacity is exceeded, `handle_capacity_overflow` only allows space to be reclaimed via `try_make_space`, and only when the incoming transaction is not itself "creating a gap": [2](#0-1) 

`try_make_space` in turn can only evict accounts drawn from `get_evictable_account`, which iterates exclusively over `self.accounts_with_gap`: [3](#0-2) 

Membership in `accounts_with_gap` is computed by `update_accounts_with_gap`. Critically, if a delayed declare transaction exists at the account's current nonce, the function short-circuits and marks the account as **not** having a gap — regardless of whether the account also holds numerous higher-nonce transactions that cannot be executed until the delayed declare matures and is promoted: [4](#0-3) 

Because such an account is excluded from `accounts_with_gap`, `get_evictable_account` will never select it, and `try_make_space` can never reclaim the bytes its transactions occupy — even though those transactions are functionally equivalent to a "stuck"/gapped account (unexecutable until the delayed declare's `declare_delay` window elapses). This is the same class of bug as the OpenQ finding: the enforcement of a hard capacity limit (`TOKEN_ADDRESS_LIMIT` there, mempool `capacity_in_bytes` here) depends on the *order/composition* of prior operations rather than on a consistent notion of "is this slot legitimately reclaimable", letting an attacker occupy the limited resource in a form the reclamation path cannot touch.

### Impact Explanation
An attacker (any account able to submit transactions through the gateway to the mempool, requiring no special privilege) can:
1. Submit a Declare transaction at their current account nonce (subject to the `declare_delay`, configured as 20s in production: `mempool_config.static_config.declare_delay`) [5](#0-4) .
2. Submit many subsequent higher-nonce transactions from the same account, filling the mempool's byte capacity.
3. Because `delayed_declares.contains(address, account_nonce)` is true, `update_accounts_with_gap` never marks this account as gapped, so `accounts_with_gap` never includes it.
4. Once the mempool reaches capacity, any new legitimate transaction from another account that itself is not "creating a gap" will call `try_make_space`, but `get_evictable_account` finds no reclaimable account (or only other, smaller gap accounts), and the new transaction is rejected with `MempoolError::MempoolFull` [6](#0-5) .

This can render the mempool effectively unable to admit new legitimate transactions — a network unable to confirm new transactions from other senders — until the attacker's declare delay window naturally rotates the account's status (and even then, the attacker can repeat the pattern to keep the mempool saturated with their own unexecutable transaction backlog).

### Likelihood Explanation
The attack requires only the ability to submit ordinary transactions (a Declare plus a sequence of higher-nonce transactions) through the standard gateway path — no elevated privilege, no compromised proposer/validator, and no p2p manipulation. The `declare_delay` window (20s in the default deployment config) gives a comfortable window in which the attacker's account is shielded from eviction while its transactions accumulate mempool capacity. Any account with sufficient balance to pass basic fee/nonce validation in the gateway can execute this repeatedly.

### Recommendation
Make gap/stuck-transaction detection consistent regardless of whether the nonce-0 slot is filled by a pool transaction or merely "reserved" by a delayed declare. Specifically:
- Do not exempt an account from `accounts_with_gap` solely because a delayed declare occupies its current nonce; instead, treat any account whose lowest *actually-runnable* transaction nonce doesn't match the account nonce as a genuine gap/stuck account, including when the readiness is blocked by `declare_delay`.
- Alternatively, cap the total bytes any single account (or the aggregate of delayed-declare-associated accounts) may occupy while shielded from eviction, so no attacker can monopolize `capacity_in_bytes` in a form the eviction path cannot reclaim.
- Ensure `try_make_space`'s eviction candidate set (`accounts_with_gap`) is derived directly from "can this account's next transaction be scheduled right now," not from a state (`delayed_declares.contains`) that can diverge from actual executability.

### Proof of Concept
1. Attacker account A has committed nonce 0 and sufficient balance.
2. A submits a Declare transaction at nonce 0 (accepted into `delayed_declares`, per `declare_delay` config).
3. A immediately submits transactions at nonces 1..N, sized to consume the full remaining `capacity_in_bytes` of the mempool (`exceeds_capacity` check at [7](#0-6)  passes since no overflow yet).
4. `update_accounts_with_gap` is invoked after each add; because `delayed_declares.contains(A, 0)` is true, A is never inserted into `accounts_with_gap` ( [8](#0-7) ), despite nonces 1..N being unexecutable until the declare at nonce 0 matures and is processed.
5. A benign user B now submits a valid, correctly-nonced transaction. `exceeds_capacity` returns true; `handle_capacity_overflow` calls `try_make_space`, which calls `get_evictable_account` ( [9](#0-8) ) — returning `None` because `accounts_with_gap` is empty (A is excluded, and no other account is gapped).
6. `try_make_space` returns `false`; B's transaction is rejected with `MempoolError::MempoolFull`, even though the mempool is functionally saturated with A's stuck, unexecutable transactions.

Note: I was unable to locate the `DelayedDeclares` struct's own definition and its independent expiry/pruning logic in the indexed portion of the repository (it is referenced from `mempool.rs` but its implementation file was not returned by search). If a size cap or TTL pruning specific to delayed declares exists there that limits how many bytes a single account can shield this way, it would need to be reviewed to confirm the exact exploitable capacity — I recommend a Devin session with full repository access to inspect that definition and confirm the precise byte/time bounds of the attack.

### Citations

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

**File:** crates/apollo_mempool/src/mempool.rs (L958-990)
```rust
    fn update_accounts_with_gap(&mut self, address_to_nonce: AddressToNonce) {
        for (address, account_nonce) in address_to_nonce {
            // If a delayed declare transaction exists at the account nonce, it is next to execute,
            // so no gap exists.
            if self.delayed_declares.contains(address, account_nonce) {
                self.remove_from_accounts_with_gap(address);
                continue;
            }

            // Gap exists when lowest transaction nonce is higher than account nonce.
            let gap_exists = match self.tx_pool.get_lowest_nonce(address) {
                Some(lowest_nonce) => account_nonce < lowest_nonce,
                None => false, // No transactions for the account, so no gap.
            };

            // Update the eviction tracking set accordingly.
            if gap_exists {
                if self.accounts_with_gap.insert(address) {
                    // Newly entered gap: all current pool txs for this account are now stuck.
                    let n_stuck = self.tx_pool.n_txs_for_address(address);
                    self.n_stuck_txs += n_stuck;
                    warn!(
                        "Account {address} has a nonce gap; {n_stuck} transaction(s) are now \
                         stuck."
                    );
                }
                // Stayed in gap: per-tx deltas were already applied at add/remove sites.
            } else {
                // Left gap: remaining pool txs for this account are no longer stuck.
                self.remove_from_accounts_with_gap(address);
            }
        }
    }
```

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

**File:** crates/apollo_deployments/resources/app_configs/mempool_config.json (L1-8)
```json
{
  "mempool_config.dynamic_config.transaction_ttl": 300,
  "mempool_config.static_config.capacity_in_bytes": 1073741824,
  "mempool_config.static_config.committed_nonce_retention_block_count": 100,
  "mempool_config.static_config.declare_delay": 20,
  "mempool_config.static_config.enable_fee_escalation": true,
  "mempool_config.static_config.fee_escalation_percentage": 10
}
```

**File:** crates/apollo_mempool_types/src/errors.rs (L20-21)
```rust
    #[error("Transaction rejected: mempool capacity exceeded.")]
    MempoolFull,
```
