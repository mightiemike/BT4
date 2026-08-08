### Title
Stale secondary-index entries for a live pubkey accumulate without bound, enabling unrevoked-mapping growth analogous to the unrevoked-approval bug — (File: `accounts-db/src/accounts_index/secondary.rs`, `accounts-db/src/accounts_index.rs`)

### Summary
`AccountsIndex`'s secondary indexes (`ProgramId`, `SplTokenMint`, `SplTokenOwner`) never revoke a stale "outer key → pubkey" mapping when the underlying account's owner/mint changes while the pubkey stays alive. Exactly like the `OwnableSmartWallet` bug — where only the most recently exercised approval was revoked while the earlier grant to a third party silently survived — here only the *current* value gets a fresh secondary-index mapping; every previously-observed distinct value for that pubkey remains mapped in `SecondaryIndex::index`/`reverse_index` until the pubkey fully dies (zero-lamports + cleaned while not cache-resident).

### Finding Description
`SecondaryIndex::insert` in `accounts-db/src/accounts_index/secondary.rs` adds `inner_key` under `key` and pushes `key` onto `reverse_index[inner_key]` only if not already present — it never removes the mapping created for a *previous* value of `key` for that same pubkey: [1](#0-0) 

`update_secondary_indexes` (called on every upsert/flush) simply calls `insert()` with whatever the account's current owner/mint bytes are — it has no path that removes the mapping for the account's *previous* owner/mint: [2](#0-1) 

The only removal path, `remove_by_inner_key_if` / `purge_secondary_indexes_by_inner_key_if`, is all-or-nothing: it drains **all** of a pubkey's historical outer-key mappings at once, but only fires when the pubkey's primary index entry is fully dead (`handle_dead_keys` / `purge_secondary_indexes_for_dead_keys`), and even then only if the key is not still resident in the write cache: [3](#0-2) [4](#0-3) 

This is explicitly acknowledged and tested in the codebase's own test suite: `test_get_filtered_indexed_accounts` shows that after an account is re-stored under `another_program_id`, it is "still present in the index under the original program id as well," and callers must apply a redundant post-processing filter to compensate: [5](#0-4) 

The same acknowledgment appears again for the SPL mint/owner case: [6](#0-5) 

As with `OwnableSmartWallet` — where User A's approval for User C stayed valid across an ownership round-trip because only User B's grant was revoked — here an account's *old* mint/owner mapping is never revoked when the account moves to a new mint/owner; it is only ever bulk-purged once the pubkey dies entirely and drops out of the cache. A live account (e.g. an unprivileged user's SPL token account, or any account under a program that repeatedly rewrites the owner/mint-shaped bytes at the relevant data offset across many slots) can keep generating new distinct outer-key entries every time its encoded mint/owner bytes change, each addition going through `insert_if_not_exists`/`push` with no corresponding removal for the stale value, since removal is gated entirely on full pubkey death.

### Impact Explanation
Any validator/RPC node running with `--account-index program-id|spl-token-mint|spl-token-owner` enabled is exposed. An unprivileged user fully controls the account data at the relevant offsets of an SPL token-shaped account they own, and can force many distinct historical values into the secondary index over time by repeatedly storing new tenant/mint bytes in successive slots while keeping the pubkey alive (never allowing its lamports to hit zero and get fully cleaned/evicted from cache). Each such change grows `SecondaryIndex::index` (a new outer key or expanded entry) and `SecondaryIndex::reverse_index[pubkey]`'s Vec without any corresponding eviction, causing disproportionate, unbounded memory growth for a node that never sees the actual bug-class mitigation (full pubkey death) triggered. This matches the "disproportionate storage and CPU cost" impact class, driven purely by unprivileged-user-controlled account writes into `AccountsDb`'s secondary index bookkeeping.

### Likelihood Explanation
Likelihood is moderate: it requires the operator to have enabled a secondary account index (not default), but once enabled, exploitation needs only ordinary transactions from an unprivileged account owner rewriting its own SPL-token-account-shaped data across slots — no special privilege, consensus interaction, or validator/peer role is needed.

### Recommendation
When `update_secondary_indexes` observes that a pubkey's owner/mint/program-id value has changed relative to its previously recorded value, explicitly call `remove_index_entries`/`remove_by_inner_key_if` for the pubkey's stale outer key before inserting the new mapping, rather than deferring all cleanup to full pubkey death. This requires tracking (or looking up) the previous secondary-index value(s) for a pubkey at update time, similar to how `OwnableSmartWallet`'s fix requires tracking/clearing all prior approvals rather than only the one being exercised.

### Proof of Concept
1. Enable `--account-index spl-token-mint` on a node.
2. As an unprivileged user, create an SPL-token-shaped account and repeatedly submit transactions in different slots that rewrite the bytes at the mint offset to a new unique 32-byte value each time, while keeping the account's lamports non-zero and the pubkey resident in the cache/index (i.e., never letting it fully die).
3. After N slots, observe `AccountsIndex::spl_token_mint_index.index.len()` and `reverse_index[pubkey]`'s length grow proportionally to N, with no entries ever reclaimed for old mint values, as shown in `run_test_secondary_indexes_same_slot_and_forks` where both `secondary_key1` and `secondary_key2` remain mapped to `account_key` even after `account_key` is updated to a later mint. [7](#0-6)

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L132-171)
```rust
    /// Inserts `inner_key` into `key`'s map.
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

        if self.stats.last_report.should_update(1000) {
            datapoint_info!(
                self.metrics_name,
                ("num_secondary_keys", self.index.len(), i64),
                (
                    "num_inner_keys",
                    self.stats.num_inner_keys.load(Ordering::Relaxed),
                    i64
                ),
                ("num_reverse_index_keys", self.reverse_index.len(), i64),
            );
        }
    }
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

**File:** accounts-db/src/accounts_index.rs (L856-877)
```rust
    /// Purges `inner_key` from each enabled secondary index
    pub(crate) fn purge_secondary_indexes_by_inner_key_if(
        &self,
        inner_key: &Pubkey,
        account_indexes: &AccountSecondaryIndexes,
        should_remove: impl Fn() -> bool,
    ) {
        if account_indexes.contains(&AccountIndex::ProgramId) {
            self.program_id_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }

        if account_indexes.contains(&AccountIndex::SplTokenOwner) {
            self.spl_token_owner_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }

        if account_indexes.contains(&AccountIndex::SplTokenMint) {
            self.spl_token_mint_index
                .remove_by_inner_key_if(inner_key, &should_remove);
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L2416-2454)
```rust
        check_secondary_index_mapping_correct(
            secondary_index,
            &[secondary_key1, secondary_key2],
            &account_key,
        );

        // If a later slot also introduces secondary_key1, then it should still exist in the index
        let later_slot = slot + 1;
        index.upsert(
            later_slot,
            later_slot,
            &account_key,
            true,
            &mut ReclaimsSlotList::new(),
            UPSERT_RECLAIM_TEST_DEFAULT,
        );
        index.update_secondary_indexes(
            &account_key,
            &AccountSharedData::create_from_existing_shared_data(
                0,
                Arc::new(account_data1.to_vec()),
                *token_id,
                false,
                0,
            ),
            secondary_indexes,
        );
        assert_eq!(secondary_index.get(&secondary_key1), vec![account_key]);

        // If we set a root at `later_slot`, and clean, then even though the account with secondary_key1
        // was outdated by the update in the later slot, the primary account key is still alive,
        // so both secondary keys will still be kept alive.
        let _ = index.clean_rooted_entries(&account_key, &mut ReclaimsWithNewestSlot::new(), None);

        check_secondary_index_mapping_correct(
            secondary_index,
            &[secondary_key1, secondary_key2],
            &account_key,
        );
```

**File:** accounts-db/src/accounts_db.rs (L1359-1388)
```rust
    /// Purges each key in `removed_keys` from the enabled secondary indexes, unless the key is
    /// still alive in the write cache. `removed_keys` must be keys that are not present in the
    /// primary index
    ///
    /// The cache check is all-or-nothing per key: a key kept because it is cache-live retains all
    /// of its secondary entries, including stale ones from its dead rooted versions (e.g. an old
    /// mint after the account is re-created with a new one). Scans tolerate stale entries by
    /// post-filtering against account data, and they are removed the next time the key dies while
    /// not cache-resident.
    ///
    /// Cache writes populate the secondary indexes but not the primary index, so a key that is gone
    /// from the primary index can still be alive in the write cache and must keep its secondary
    /// entries. This is tricky due to the races that need to be considered:
    /// 1) Removed from the cache then re-added to the cache by replay
    /// - This is protected by re-checking the cache in the closure passed to purge. Since purge
    ///   holds the secondary index's reverse-index lock when it re-checks cache presence, and a
    ///   cache store writes the cache before inserting into the secondary index under that same
    ///   lock, either the re-check sees the cache write and the entry is not removed, or the
    ///   removal wins and the store's later insert re-adds it.
    /// 2) Removed from the cache, and also simultaneously removed from storage by clean
    /// - Since both the cache removal and the index removal are done before the removal from the
    ///   secondary index, the worst case is a double removal (both paths remove the same secondary
    ///   index entry). This is safe since the secondary index removal is idempotent.
    /// 3) Removed from the storage, but still present in the cache
    /// - This is protected by checking the cache presence in the closure. If the pubkey is still
    ///   present in the cache, the secondary index entry is not removed.
    ///
    /// We do not need to consider removed from cache -> added to storage. Adding to storage
    /// requires a cache entry to be present first, so a fresh store of the key would have to be
    /// rooted and flushed inside this window — impossible because rooting is driven by the same
```

**File:** runtime/src/bank/tests.rs (L3530-3533)
```rust
    // Even though the account is re-stored in the bank (and the index) under a new program id,
    // it is still present in the index under the original program id as well. This
    // demonstrates the need for a redundant post-processing filter.
    let another_program_id = Pubkey::new_unique();
```
