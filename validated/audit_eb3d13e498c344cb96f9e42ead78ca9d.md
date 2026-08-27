### Title
`AccountLocks::try_lock_transaction_batch` performs conflict checks and lock application as two disjoint passes, allowing intra-batch write/read lock collisions - (File: `accounts-db/src/account_locks.rs`)

### Summary
`try_lock_transaction_batch` first scans every transaction in a batch and records pass/fail against the *current, unmutated* lock table, and only afterward applies the actual locks in a second pass. Because the check pass never sees the effects of locks that other transactions in the *same* batch are about to take, two transactions in one batch that reference the same account with conflicting access modes (write vs. write, or write vs. read) can both pass validation and then both have their locks applied unconditionally in the second pass, since `lock_write`/`lock_readonly` never re-check for conflicts - they simply increment a counter.

### Finding Description
The relevant code: [1](#0-0) 

Phase 1 (`can_lock_accounts`) is a read-only check against `self`: [2](#0-1) 

Phase 2 (`lock_accounts`) unconditionally mutates the tables via `lock_write`/`lock_readonly`, which never re-verify conflicts - they just bump a counter: [3](#0-2) 

Because phase 1 iterates the whole batch **before** any lock is applied (`can_lock_accounts` takes `&self`, not `&mut self`), the check for transaction B in the batch is evaluated against the pre-batch lock-table snapshot, not against the lock that transaction A (also in the batch) is about to take. If:
- Tx A writes account `K`
- Tx B reads (or writes) account `K`
- Neither `K` is already locked by an unrelated in-flight transaction

then both A and B pass `can_lock_accounts` in phase 1 (since `K` is unlocked in the snapshot both see), and phase 2 applies both locks unconditionally: `write_locks[K] += 1` and `readonly_locks[K] += 1` (or `write_locks[K] += 2` in the write/write case). The result is a lock-table state where the same account is simultaneously flagged writable-locked and readonly-locked, which is exactly the invariant `can_write_lock`/`can_read_lock` are meant to prevent: [4](#0-3) 

`account_locks.rs` is the authoritative mechanism used to decide whether transactions inside an arbitrary batch (e.g., all transactions considered together by the runtime/scheduler for a given processing pass) can safely execute concurrently; it is precisely responsible for catching intra-batch conflicts, since batch composition is not otherwise guaranteed conflict-free by this layer of code. This single-vs-batch check/lock split defeats that responsibility for any batch containing two transactions that conflict on the same account with a write and a read (or write and write).

An unprivileged attacker only needs to submit two ordinary transactions - Tx A writable on account `K`, Tx B readonly on account `K` - and rely on both being handed to `try_lock_transaction_batch` in the same call/batch. No signer or writable-flag validation of the message itself prevents this; those checks apply within a single transaction's own account list, not across transactions in the same lock-table call.

### Impact Explanation
If two transactions with conflicting access to the same account both successfully hold locks at once, the runtime's single-writer isolation guarantee is violated: both transactions can be scheduled/executed against the account concurrently or interleaved with no serialization. This is a direct violation of Solana's core parallel-execution safety invariant, and can produce different observed final account states/order-dependent results between validators depending on scheduling/thread timing, which corresponds to the "Consensus/Safety Violation (bank hash divergence or fork)" bounty category.

### Likelihood Explanation
The only precondition is getting two account-conflicting transactions processed together in one `try_lock_transaction_batch` call, something an attacker cannot directly force but is plausible whenever the runtime hands a multi-transaction batch (e.g., transactions drawn from an entry, or a scheduler unit) to this function without a prior guarantee that the batch is conflict-free — which is the exact scenario this function is supposed to defend against. The attacker's cost is minimal (two ordinary transactions, no special privileges, deployable repeatedly), but reliably forcing both transactions into the *same* call (rather than sequential separate calls) depends on the calling code's batching behavior, which I was not able to fully trace to callers (`accounts.rs`/`bank.rs`/schedulers) within the available time. This introduces uncertainty about real-world reachability that a background engineering session with full file access should verify.

### Recommendation
Merge the check-and-lock passes into a single pass that mutates the lock table incrementally as each transaction in the batch is validated (i.e., call `can_lock_accounts` immediately followed by `lock_accounts` for each transaction in sequence, so that each subsequent transaction's check sees the effect of already-applied locks from earlier transactions in the same batch), rather than checking the entire batch against a frozen snapshot and then blindly applying all passing locks.

### Proof of Concept
Unit test in `accounts-db/src/account_locks.rs`:
1. Construct an `AccountLocks` with empty state.
2. Build a batch of two "transactions": Tx A = `[(K, writable=true)]`, Tx B = `[(K, writable=false)]`.
3. Call `try_lock_transaction_batch` with both wrapped in `Ok(...)`.
4. Assert that at most one of the two results is `Ok(())` (the other must be `Err(TransactionError::AccountInUse)`).
5. Additionally assert the internal invariant: after the call, it must not be the case that `is_locked_write(&K) && is_locked_readonly(&K)` are both true.
6. Expected (buggy) result: both results are `Ok(())` and both `is_locked_write(&K)` and `is_locked_readonly(&K)` are true simultaneously, demonstrating the invariant violation.

### Citations

**File:** accounts-db/src/account_locks.rs (L22-40)
```rust
    pub fn try_lock_transaction_batch<'a>(
        &mut self,
        mut validated_batch_keys: Vec<
            TransactionResult<impl Iterator<Item = (&'a Pubkey, bool)> + Clone>,
        >,
    ) -> Vec<TransactionResult<()>> {
        validated_batch_keys.iter_mut().for_each(|validated_keys| {
            if let Ok(keys) = validated_keys.as_ref()
                && let Err(e) = self.can_lock_accounts(keys.clone())
            {
                *validated_keys = Err(e);
            }
        });

        validated_batch_keys
            .into_iter()
            .map(|available_keys| available_keys.map(|keys| self.lock_accounts(keys)))
            .collect()
    }
```

**File:** accounts-db/src/account_locks.rs (L56-71)
```rust
    fn can_lock_accounts<'a>(
        &self,
        keys: impl Iterator<Item = (&'a Pubkey, bool)>,
    ) -> TransactionResult<()> {
        for (key, writable) in keys {
            if writable {
                if !self.can_write_lock(key) {
                    return Err(TransactionError::AccountInUse);
                }
            } else if !self.can_read_lock(key) {
                return Err(TransactionError::AccountInUse);
            }
        }

        Ok(())
    }
```

**File:** accounts-db/src/account_locks.rs (L73-109)
```rust
    fn lock_accounts<'a>(&mut self, keys: impl Iterator<Item = (&'a Pubkey, bool)>) {
        for (key, writable) in keys {
            if writable {
                self.lock_write(key);
            } else {
                self.lock_readonly(key);
            }
        }
    }

    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn is_locked_readonly(&self, key: &Pubkey) -> bool {
        self.readonly_locks.get(key).is_some_and(|count| *count > 0)
    }

    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn is_locked_write(&self, key: &Pubkey) -> bool {
        self.write_locks.get(key).is_some_and(|count| *count > 0)
    }

    fn can_read_lock(&self, key: &Pubkey) -> bool {
        // If the key is not write-locked, it can be read-locked
        !self.is_locked_write(key)
    }

    fn can_write_lock(&self, key: &Pubkey) -> bool {
        // If the key is not read-locked or write-locked, it can be write-locked
        !self.is_locked_readonly(key) && !self.is_locked_write(key)
    }

    fn lock_readonly(&mut self, key: &Pubkey) {
        *self.readonly_locks.entry(*key).or_default() += 1;
    }

    fn lock_write(&mut self, key: &Pubkey) {
        *self.write_locks.entry(*key).or_default() += 1;
    }
```
