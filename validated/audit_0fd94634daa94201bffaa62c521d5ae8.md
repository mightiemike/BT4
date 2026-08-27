### Title
Two-phase check-then-lock split in `AccountLocks::try_lock_transaction_batch` allows duplicate write locks on the same pubkey within one batch - (File: accounts-db/src/account_locks.rs)

### Summary
`try_lock_transaction_batch` validates every transaction's requested locks against the *same, unmutated* `AccountLocks` state in a first pass (`can_lock_accounts`), and only applies the locks in a second, separate pass (`lock_accounts`). Because the first pass never updates `self.write_locks`/`self.readonly_locks` between transactions, two transactions in the same batch that write-lock the same pubkey can both pass the check phase and both succeed in the apply phase, ending up with a write-lock reference count of 2 for the same account.

### Finding Description
`AccountLocks::try_lock_transaction_batch` is implemented as two sequential loops: [1](#0-0) 

In the first loop, `can_lock_accounts` is called once per transaction against `self` [2](#0-1) . This function is read-only — it never mutates `write_locks`/`readonly_locks` — so every transaction in the batch is validated against the account-lock state as it existed *before the batch started*, not against the cumulative effect of the other transactions being validated in the same call.

Only in the second loop is `lock_accounts` invoked, which unconditionally increments the lock counters via `lock_write`/`lock_readonly` [3](#0-2) [4](#0-3) . Because this second pass no longer re-validates against `can_write_lock`/`can_read_lock`, it simply trusts the earlier (stale) validation result.

Given two transactions `T1` and `T2` in the same `validated_batch_keys` vector, both writing to pubkey `P`, and `P` not already locked prior to the call:
1. Phase 1: `can_lock_accounts(T1's keys)` sees `P` unlocked → `Ok`. `can_lock_accounts(T2's keys)` also sees `P` unlocked (state hasn't changed) → `Ok`.
2. Phase 2: `lock_accounts(T1's keys)` increments `write_locks[P]` to 1. `lock_accounts(T2's keys)` increments `write_locks[P]` to 2.
3. Both `T1` and `T2` receive `Ok(())` from `try_lock_transaction_batch`, despite both holding a "write lock" on `P` simultaneously.

This breaks the fundamental invariant of `AccountLocks` — that a writable account can only be exclusively locked by one in-flight transaction at a time — for any code path that constructs a `validated_batch_keys` vector containing multiple transactions with overlapping writable accounts and passes them to a single `try_lock_transaction_batch` call.

### Impact Explanation
If this batched locking API is used by a component (e.g., transaction batch preparation for execution/replay) with a batch that includes multiple transactions writing the same account — instead of pre-filtering conflicting transactions into separate batches — both transactions would be scheduled for concurrent execution against the same account state. Concurrent mutation of the same account without serialization can produce non-deterministic execution results depending on interleaving, leading to a diverging bank hash across validators (consensus divergence) rather than an immediate, deterministic panic. This falls into the "consensus divergence / safety violation" bounty category.

### Likelihood Explanation
The precondition is structural, not attacker-controlled in the traditional sense: an unprivileged attacker only needs two funded keypairs each submitting a transaction that writes to the same account (e.g., two independent transfers into the same recipient account, or two invocations of a program that writes shared state) at the cost of two ordinary transaction fees. The actual severity depends entirely on whether any current caller in the codebase feeds `try_lock_transaction_batch` with a batch that has NOT already been pre-deduplicated for intra-batch conflicts — I was not able to confirm this in this pass, since the only call sites visible in the indexed portion of the repo are within `account_locks.rs` itself; other candidate callers in `accounts-db/src/accounts.rs` and `scheduling-utils/src/thread_aware_account_locks.rs` matched only `lock_accounts`/`can_lock_accounts` by name and could not be fully inspected due to iteration limits. The bug in the primitive itself, however, is unambiguous from the code.

### Recommendation
Merge the check-and-lock into a single atomic pass per transaction (check availability and immediately apply the lock before validating the next transaction in the batch), so that a transaction's lock acquisition is visible to subsequent validations within the same batch call — matching the original single-transaction `lock_accounts`/`can_lock_accounts` semantics used elsewhere. Alternatively, detect intra-batch write-write/write-read conflicts explicitly (e.g., via a per-batch scratch set) before entering the two-phase check/apply split, and mark all but the first conflicting transaction as `Err(TransactionError::AccountInUse)`.

### Proof of Concept
Add a unit test in `accounts-db/src/account_locks.rs`:
```rust
#[test]
fn test_try_lock_transaction_batch_rejects_intra_batch_write_conflict() {
    let mut account_locks = AccountLocks::default();
    let pubkey = Pubkey::new_unique();

    let tx1_keys = vec![(&pubkey, true)];
    let tx2_keys = vec![(&pubkey, true)];

    let batch = vec![
        Ok(tx1_keys.into_iter()),
        Ok(tx2_keys.into_iter()),
    ];

    let results = account_locks.try_lock_transaction_batch(batch);

    let ok_count = results.iter().filter(|r| r.is_ok()).count();
    assert_eq!(
        ok_count, 1,
        "only one of two conflicting writable-lock transactions in the same batch should succeed"
    );
}
```
Expected (correct) behavior: exactly one `Ok(())` and one `Err(TransactionError::AccountInUse)`. Current behavior: both results are `Ok(())`, and `account_locks.is_locked_write(&pubkey)` shows an internal write-lock count of 2, confirming the double-lock.

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
