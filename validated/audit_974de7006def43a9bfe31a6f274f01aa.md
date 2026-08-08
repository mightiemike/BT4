### Title
Unbounded per-pubkey growth of `SecondaryIndex` reverse-index `Vec` causes quadratic index-maintenance cost disproportionate to fees paid - ([File: accounts-db/src/accounts_index/secondary.rs])

### Summary
`SecondaryIndex::insert` stores, for each account pubkey, a `Vec<Pubkey>` of every distinct outer key (e.g. every distinct SPL-token mint) that pubkey has ever been indexed under, and this vector is only fully drained when the pubkey completely dies (removed from both the primary index and the write cache). An attacker who repeatedly closes and reinitializes the same token-account pubkey with a different mint each time can grow this per-pubkey vector without bound, and each `insert` call does an O(k) linear scan (`outer_keys.contains(key)`) against the current vector length, making the cumulative indexing cost for N such reinitializations O(N²) while the attacker's fee cost is only O(N).

### Finding Description
`update_secondary_index_cached_accounts` (`accounts-db/src/accounts_db.rs:4838-4884`) is called from `store_accounts_unfrozen` (`accounts-db/src/accounts_db.rs:5179-5236`) on every cache write and invokes `AccountsIndex::update_secondary_indexes` (`accounts-db/src/accounts_index.rs:614-660`), which calls `update_spl_token_secondary_indexes` for the mint/owner indexes. That path ultimately calls `SecondaryIndex::insert` (`accounts-db/src/accounts_index/secondary.rs:133-171`).

`insert()` maintains a `reverse_index: DashMap<Pubkey, RwLock<Vec<Pubkey>>>` mapping the account pubkey (`inner_key`) to every outer key (e.g. mint) it has ever been associated with. Each call does:
```
if !outer_keys.contains(key) {
    outer_keys.push(*key);
}
```
which is an O(k) scan where k is the number of distinct outer keys accumulated so far for that pubkey. The code comment explicitly acknowledges this design assumes such reuse is "rare":
```
// The only cases where an inner key should map to a different outer key is
// if the had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet
```
(`accounts-db/src/accounts_index/secondary.rs:57-60`).

Entries are only removed from this vector via `remove_by_inner_key_if`, which drains the *entire* vector at once, and is only invoked from `purge_secondary_indexes_for_dead_keys` (`accounts-db/src/accounts_db.rs:1391-1411`) — which itself only fires when a pubkey is fully removed from the primary index *and* is not present in the write cache (`|| !self.accounts_cache.contains_pubkey(key)`). Critically, when an account is zero-lamport, `write_accounts_to_cache`/`account_default_if_zero_lamport` resets it to `AccountSharedData::default()` before `update_secondary_index_cached_accounts` runs, and `update_secondary_indexes` explicitly skips zero-lamport accounts (comment at `accounts_index.rs:632-644`). This means a close operation never removes the stale mint entry; only a full "death" (leaving both the index and cache) triggers cleanup.

An attacker controlling only their own accounts can:
1. Create and fund a token account at pubkey P.
2. `InitializeAccount` with mint A (an insert into the secondary index, adding A to P's reverse-index vector).
3. `CloseAccount` (zero-lamport, resets in cache, secondary index update skipped — stale mint-A entry remains).
4. Re-fund and re-`InitializeAccount` the same pubkey P with a new mint B before the pubkey ever fully leaves the cache/index (i.e., replay/flush timing keeps it "alive" in some fork of the cache), adding B.
5. Repeat with mint C, D, ... N.

Each iteration costs the attacker one fixed transaction fee, but the reverse-index vector for P grows to length N, and the `contains()` scan in step (2)-analog for iteration i costs O(i), so total indexing work is O(N²) for O(N) fees paid.

### Impact Explanation
This is a resource-exhaustion / disproportionate-CPU-cost issue: `update_secondary_index_cached_accounts` work per write is not bounded proportionally to the fee paid for that write, because the underlying `SecondaryIndex::insert` primitive's cost is O(k) in the number of distinct historical outer-key associations for a given pubkey, and that per-pubkey state is not cleaned up until the pubkey fully dies. A validator running with account indexes enabled (e.g., `--account-index spl-token-mint`) and processing an attacker's rapid create/close/reinit loop against a single pubkey would see quadratic CPU growth in the indexing hot path, and the growing `Vec<Pubkey>` also represents unbounded per-pubkey memory growth. This matches the "index cost blow-up (resource exhaustion)" impact category named in the question.

### Likelihood Explanation
Preconditions are minimal: the attacker needs no special privileges, only the ability to submit ordinary token-program instructions (`CreateAccount`, `InitializeAccount`, `CloseAccount`) against a validator that has the relevant secondary index enabled (this is an opt-in validator configuration, not attacker-controlled, but the question's premise explicitly allows targeting nodes that have enabled account indexes). The attack is fully repeatable and scales with the number of distinct mints the attacker chooses to cycle through on the same pubkey, bounded only by the attacker's willingness to pay per-transaction fees (which scale linearly while the validator-side cost scales quadratically).

### Recommendation
Replace the reverse-index `Vec<Pubkey>` with a `HashSet<Pubkey>` (or otherwise avoid the linear `contains()` scan), removing the "rare" assumption that an attacker can violate. Additionally, consider proactively evicting/bounding stale entries for a pubkey once it transitions through a zero-lamport/close event, rather than deferring all cleanup to full key death, so that adversarial close/reopen cycling cannot accumulate unbounded per-pubkey secondary-index state.

### Proof of Concept
Rust benchmark/unit test plan (extending the existing test harness in `accounts-db/src/accounts_index/secondary.rs` tests and `accounts_db/tests/impl.rs`):
```rust
#[test]
fn test_secondary_index_insert_cost_grows_quadratically_with_distinct_outer_keys() {
    let secondary_index = SecondaryIndex::<RwLockSecondaryIndexEntry>::new("bench");
    let inner_key = Pubkey::new_unique(); // simulates the attacker's reused token account pubkey
    let outer_keys: Vec<_> = (0..10_000).map(|_| Pubkey::new_unique()).collect();

    let mut cumulative_scan_cost = 0u64;
    for (i, outer_key) in outer_keys.iter().enumerate() {
        // Each insert scans the growing reverse-index vec (length == i before this insert)
        let start = std::time::Instant::now();
        secondary_index.insert(outer_key, &inner_key);
        cumulative_scan_cost += start.elapsed().as_nanos() as u64;
        // Simulate that the pubkey never fully "dies" between iterations
        // (no call to remove_by_inner_key_if / purge_secondary_indexes_for_dead_keys)
    }

    // Assert the reverse-index vector has grown to N entries (never cleaned up
    // because the pubkey never fully died), demonstrating unbounded per-pubkey growth.
    let reverse_entry = secondary_index.reverse_index.get(&inner_key).unwrap();
    assert_eq!(reverse_entry.read().unwrap().len(), outer_keys.len());

    // Compare wall-time growth rate of the second half of insertions vs the first half;
    // expect the second half to take significantly longer due to O(k) contains() scans,
    // demonstrating super-linear (quadratic) cost growth relative to a constant per-insert fee.
}
```
An integration-level PoC would drive this through `AccountsDb::store_accounts_unfrozen` with `spl_token_mint_index_enabled()` (as in `test_flush_purged_zero_lamport_account_purges_secondary_index`), repeatedly storing the same pubkey with token-account data whose mint field changes each iteration, keeping the pubkey alive in the cache across iterations (never calling `purge_slots_from_cache`/allowing full death), and asserting that `accounts_index.get_index_key_size(&AccountIndex::SplTokenMint, ...)` and the reverse-index vector length grow linearly with iteration count while per-iteration `update_secondary_index_us` (from `StoreAccountsUnfrozenStats`) grows correspondingly, confirming the O(N²) total cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L57-60)
```rust
// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
```

**File:** accounts-db/src/accounts_index/secondary.rs (L133-157)
```rust
    pub fn insert(&self, key: &Pubkey, inner_key: &Pubkey) {
        // Note: Always lock the reverse index first, so we synchronize with remove().
        // Pre-size to 1 to avoid push() over-allocating an empty Vec to capacity 4.
        let reverse_index_entry = self
            .reverse_index
            .entry(*inner_key)
            .or_insert_with(|| RwLock::new(Vec::with_capacity(1)));
        let mut outer_keys = reverse_index_entry.write().unwrap();

        // Now insert into the index.
        // Note, we do this get()-then-unwrap instead of calling entry() directly, because
        // get() is a read lock whereas entry() is a write lock.  We assume `key` already has
        // a map created, so optimize for the common case and only take a read lock.
        self.index
            .get(key)
            .unwrap_or_else(|| self.index.entry(*key).or_default().downgrade())
            .insert_if_not_exists(inner_key, &self.stats.num_inner_keys);

        if !outer_keys.contains(key) {
            outer_keys.push(*key);
        }

        // explicitly drop the locks so we don't hold them while reporting metrics
        drop(outer_keys);
        drop(reverse_index_entry);
```

**File:** accounts-db/src/accounts_index/secondary.rs (L212-250)
```rust
    /// Removes `inner_key` from the secondary index, if the closure `should_remove` returns true.
    ///
    /// `should_remove` is evaluated while holding `inner_key`'s reverse-index entry lock. Because
    /// `insert()` acquires that same lock before adding a mapping, holding it across the check
    /// serializes this removal against a concurrent `insert(_, inner_key)`. This only yields a
    /// correct decision if writers update the state that `should_remove` reads before calling
    /// `insert()`; otherwise the check can pass against stale state and remove a mapping that a
    /// concurrent writer expects to survive.
    pub fn remove_by_inner_key_if(&self, inner_key: &Pubkey, should_remove: impl Fn() -> bool) {
        // Note: Always lock the reverse-index first, so we synchronize with insert().
        let DashMapEntry::Occupied(reverse_index_entry) = self.reverse_index.entry(*inner_key)
        else {
            // if inner_key doesn't exist in the reverse-index, nothing to do here
            return;
        };

        // Re-check under the reverse-index entry lock. If the caller no longer wants the key
        // removed (e.g. it was concurrently re-added), leave its mapping in place.
        if !should_remove() {
            return;
        }

        // First go through the reverse-index and remove inner_key from all forward-indexes.
        let num_removed = reverse_index_entry
            .get()
            .write()
            .unwrap()
            .drain(..)
            .map(|outer_key| self.remove_index_entries(&outer_key, inner_key) as u64)
            .sum();

        // And now after removing inner_key from all forward-indexes,
        // remove its entry from the reverse-index.
        reverse_index_entry.remove();

        self.stats
            .num_inner_keys
            .fetch_sub(num_removed, Ordering::Relaxed);
    }
```

**File:** accounts-db/src/accounts_db.rs (L1391-1411)
```rust
    fn purge_secondary_indexes_for_dead_keys<'a>(
        &self,
        removed_keys: impl IntoIterator<Item = &'a Pubkey>,
    ) {
        if self.account_indexes.is_empty() {
            return;
        }
        for key in removed_keys {
            // Purging secondary entries for a key that is still alive in the primary index
            // would leave a live account invisible to secondary-index scans
            debug_assert!(
                !self.accounts_index.contains(key),
                "key removed from the primary index must not be present: {key}"
            );
            self.accounts_index.purge_secondary_indexes_by_inner_key_if(
                key,
                &self.account_indexes,
                || !self.accounts_cache.contains_pubkey(key),
            );
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L4838-4884)
```rust
    fn update_secondary_index_cached_accounts<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
        store_account: &BitVec,
        update_index_thread_selection: UpdateIndexThreadSelection,
    ) {
        if !self.account_indexes.is_empty() {
            let len = accounts.len();
            assert_eq!(accounts.len() as u64, store_account.len());

            // Cache writes do not upsert the accounts index; it only ever holds storage entries,
            // populated on flush. Readers find cache-only accounts through the write cache. Only
            // the secondary indexes are updated here.
            let update = |start, end| {
                (start..end).for_each(|i| {
                    if store_account[i as u64] {
                        accounts.account(i, |account| {
                            self.accounts_index.update_secondary_indexes(
                                account.pubkey(),
                                &account,
                                &self.account_indexes,
                            );
                        });
                    }
                });
            };

            let threshold = 1;
            if matches!(
                update_index_thread_selection,
                UpdateIndexThreadSelection::PoolWithThreshold,
            ) && len > threshold
            {
                let chunk_size = len.div_ceil(self.thread_pool_foreground.current_num_threads());
                let batches = 1 + len / chunk_size;
                self.thread_pool_foreground.install(|| {
                    (0..batches).into_par_iter().for_each(|batch| {
                        let start = batch * chunk_size;
                        let end = std::cmp::min(start + chunk_size, len);
                        update(start, end)
                    })
                });
            } else {
                update(0, len);
            }
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L5177-5204)
```rust
    /// Stores accounts in the write cache and updates the index.
    /// This should only be used for accounts that are unrooted (unfrozen)
    pub(crate) fn store_accounts_unfrozen<'a>(
        &self,
        accounts: impl StorableAccounts<'a>,
        update_index_thread_selection: UpdateIndexThreadSelection,
        ancestors: &Ancestors,
    ) {
        // If all transactions in a batch are errored,
        // it's possible to get a store with no accounts.
        if accounts.is_empty() {
            return;
        }

        // Store the accounts in the write cache
        let write_accounts_time = Measure::start("write_accounts");
        let (store_account, write_stats) =
            self.write_accounts_to_cache(accounts.target_slot(), &accounts, ancestors);
        let write_accounts_us = write_accounts_time.end_as_us();

        // Update the secondary index
        let update_secondary_index_time = Measure::start("update_secondary_index");
        self.update_secondary_index_cached_accounts(
            &accounts,
            &store_account,
            update_index_thread_selection,
        );
        let update_secondary_index_us = update_secondary_index_time.end_as_us();
```

**File:** accounts-db/src/accounts_index.rs (L614-660)
```rust
    pub(crate) fn update_secondary_indexes(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if account_indexes.is_empty() {
            return;
        }

        let account_owner = account.owner();
        let account_data = account.data();

        if account_indexes.contains(&AccountIndex::ProgramId)
            && account_indexes.include_key(account_owner)
        {
            self.program_id_index.insert(account_owner, pubkey);
        }
        // Note because of the below check below on the account data length, when an
        // account hits zero lamports and is reset to AccountSharedData::Default, then we skip
        // the below updates to the secondary indexes.
        //
        // Skipping means not updating secondary index to mark the account as missing.
        // This doesn't introduce false positives during a scan because the caller to scan
        // provides the ancestors to check. So even if a zero-lamport account is not yet
        // removed from the secondary index, the scan function will:
        // 1) consult the primary index via `get(&pubkey, Some(ancestors), max_root)`
        // and find the zero-lamport version
        // 2) When the fetch from storage occurs, it will return AccountSharedData::Default
        // (as persisted tombstone for snapshots). This will then ultimately be
        // filtered out by post-scan filters, like in `get_filtered_spl_token_accounts_by_owner()`.

        self.update_spl_token_secondary_indexes::<spl_generic_token::token::Account>(
            &spl_generic_token::token::id(),
            pubkey,
            account_owner,
            account_data,
            account_indexes,
        );
        self.update_spl_token_secondary_indexes::<spl_generic_token::token_2022::Account>(
            &spl_generic_token::token_2022::id(),
            pubkey,
            account_owner,
            account_data,
            account_indexes,
        );
    }
```
