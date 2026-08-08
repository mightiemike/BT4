### Title
Intra-batch write/write (or write/read) lock conflicts are not detected in `AccountLocks::try_lock_transaction_batch`, allowing two conflicting transactions in the same batch to both be granted a lock - (File: `accounts-db/src/account_locks.rs`)

### Summary
`try_lock_transaction_batch` validates every transaction's account keys in a first pass using the read-only `can_lock_accounts` check, and only *afterwards*, in a second pass, actually applies the locks via `lock_accounts`. Because the validation pass never mutates `self` between transactions, two (or more) transactions in the *same* batch that write (or write/read) the same key are both validated against the identical pre-batch lock state and both pass, even though they conflict with each other. Both are then locked in the second pass, violating the write-exclusivity invariant the struct is supposed to enforce.

### Finding Description
The function is implemented as two separate passes: [1](#0-0) 

In the first pass, `can_lock_accounts` is `&self` (read-only) and does not mutate `write_locks`/`readonly_locks`: [2](#0-1) 

Only the second pass, which iterates the *same already-checked* vector and calls `lock_accounts` (`&mut self`), actually increments the lock counters: [3](#0-2) [4](#0-3) 

Consequence: if the batch contains, e.g., `tx0` writing key `A` and `tx1` also writing key `A`, the first pass calls `self.can_lock_accounts` for `tx0` (sees `A` unlocked → `Ok`) and then for `tx1` (still sees `A` unlocked, because `tx0`'s lock was never applied to `self` in this pass → also `Ok`). Both entries remain `Ok(..)` in `validated_batch_keys`. The second pass then calls `lock_accounts` for both, incrementing `write_locks[A]` to `2`. The function returns `Ok(())` for *both* `tx0` and `tx1`, even though they write the same account, directly violating the function's own doc comment: "Lock accounts for all transactions in a batch which don't conflict with existing locks." The check only guards against locks that existed *before* entering this call, not against conflicts introduced by earlier transactions within the same batch.

This is a genuine implementation defect independent of any external guard (slot/ancestor, zero-lamport, ref-count) because it lives entirely inside the in-memory account-lock bookkeeping layer that the execution engine trusts to serialize concurrent writers.

### Impact Explanation
Any caller (e.g. the unified scheduler / banking stage) that relies on an `Ok` result from `try_lock_transaction_batch` as a guarantee of exclusive write access will schedule both conflicting transactions for concurrent execution against the same account. This can produce a lost-update/data race on the account's bytes, non-deterministic final account state depending on thread interleaving, and divergence in the resulting lt-hash/capitalization between honest validators with different scheduling — matching the "hash/capitalization divergence" bounty category. An unprivileged user can trigger the precondition simply by submitting enough transactions that write the same account key so that the scheduler groups them into a single lock-check batch.

### Likelihood Explanation
The bug is deterministic given the described preconditions (a batch containing 2+ transactions that write the same key), not probabilistic — it triggers on the very first pass through the function whenever such a batch is formed. It only requires that some caller pass a batch containing conflicting keys to `try_lock_transaction_batch` (its own doc comment implies this exact case should be handled/rejected), and an attacker fully controls which accounts their own transactions touch and how many they submit, satisfying the unprivileged-attacker constraint. The remaining uncertainty is whether the current scheduler always splits conflicting transactions into different batches before calling this function (which would make the bug latent/dead-code today); this could not be fully confirmed from the available `accounts.rs` context within the tool budget, so likelihood should be evaluated by a Devin session that traces every caller of `try_lock_transaction_batch`.

### Recommendation
Merge the check-and-lock passes so that `can_lock_accounts` and `lock_accounts` are applied per-transaction against the *incrementally updated* lock state, e.g.:
```rust
validated_batch_keys
    .into_iter()
    .map(|available_keys| {
        available_keys.and_then(|keys| {
            self.can_lock_accounts(keys.clone())?;
            self.lock_accounts(keys);
            Ok(())
        })
    })
    .collect()
```
This ensures each transaction's lock check reflects all locks already granted to earlier transactions in the same batch.

### Proof of Concept
```rust
#[test]
fn test_intra_batch_conflicting_writes_both_locked() {
    use solana_pubkey::Pubkey;
    let mut locks = AccountLocks::default();
    let key_a = Pubkey::new_unique();

    let tx0_keys = vec![(&key_a, true)]; // writable
    let tx1_keys = vec![(&key_a, true)]; // writable, conflicts with tx0

    let batch = vec![
        Ok(tx0_keys.into_iter()),
        Ok(tx1_keys.into_iter()),
    ];

    let results = locks.try_lock_transaction_batch(batch);

    // BUG: both succeed even though they write the same key.
    assert!(results[0].is_ok());
    assert!(results[1].is_ok()); // should be Err(AccountInUse) but currently passes

    // Underlying invariant violated: write lock count for `key_a` is 2,
    // meaning two "exclusive" writers hold the lock simultaneously.
    assert!(locks.is_locked_write(&key_a));
    // (exposing write_locks count via a dev-only accessor would show 2)
}
```
Expected (fixed) behavior: the second assertion should be `results[1] == Err(TransactionError::AccountInUse)`, and only one of the two transactions should ever hold the write lock. A stress/fuzz variant should generate large batches with random overlapping writable/readonly keys and assert that at most one writer (and no writer+reader) is ever concurrently locked for any given key, verifying `write_locks`/`readonly_locks` counts always correspond to exactly the transactions that were granted `Ok`.

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

**File:** accounts-db/src/account_locks.rs (L73-81)
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
```

**File:** accounts-db/src/account_locks.rs (L103-109)
```rust
    fn lock_readonly(&mut self, key: &Pubkey) {
        *self.readonly_locks.entry(*key).or_default() += 1;
    }

    fn lock_write(&mut self, key: &Pubkey) {
        *self.write_locks.entry(*key).or_default() += 1;
    }
```
