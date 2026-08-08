### Title
Non-atomic `write_through` disk write allows a stale `AccountInfo` to overwrite a newer one when `remove_unrooted_slots`'s cache-purge write-through races `shrink_storage`'s index `replace()` - (`accounts-db/src/accounts_index/in_mem_accounts_index.rs`)

### Summary
When the accounts index is configured with disk write-through (`IndexLimit::Threshold`), `write_through()` writes an `AccountInfo` to the on-disk bucket *before* checking whether the in-memory entry still matches the value being written. If `remove_unrooted_slots`'s `purge_slots_from_cache` → `write_through_pubkeys` → `try_write_through` captures a pubkey's single-ref entry for a rooted-and-flushed slot at the same moment `shrink_storage`'s `update_index_for_shrink` → `AccountsIndex::replace()` rewrites that same entry to point at the post-shrink storage, the two `write_through()` calls can complete their disk writes out of order, leaving the on-disk bucket holding a stale `AccountInfo` that references storage `shrink_storage` has already dropped.

### Finding Description
`InMemAccountsIndex::write_through()` performs the disk write unconditionally and only *afterward* checks the in-memory state to decide whether to clear the dirty flag: [1](#0-0) 

`try_write_through()` (invoked from `AccountsIndex::write_through_pubkeys` inside `purge_slots_from_cache`, called by `remove_unrooted_slots`) first reads the entry under a read-only snapshot (`get_only_in_mem`), then calls `write_through()` with the *captured* `(slot, info)` pair: [2](#0-1) 

Concurrently, `shrink_storage()` rewrites the slot's storage and calls `update_index_for_shrink` → `AccountsIndex::replace()`, which mutates the same entry's slot list to the post-shrink `AccountInfo` and, when the entry is single-ref, calls `write_through()` itself with the *new* value: [3](#0-2) [4](#0-3) 

Race sequence (both threads race on the same pubkey `P`, slot `0`):
1. `try_write_through` (purge path) reads the entry: `dirty=true, ref_count=1, slot_list=[(0, info_old)]` (points at pre-shrink storage `A`), captures `(0, info_old)`.
2. `replace()` (shrink path) locks the slot list, swaps in `info_new` (points at post-shrink storage `B`), marks dirty, then — because `ref_count==1`/`len==1` — calls `write_through(P, 0, info_new)`. This writes `(0, info_new)` to disk, then re-checks the in-mem entry, finds it matches, and clears dirty.
3. `try_write_through`'s deferred `write_through(P, 0, info_old)` now executes: it unconditionally writes the **stale** `(0, info_old)` to disk, *overwriting* the correct `info_new` entry just written in step 2. Its post-write check then compares against the *current* in-mem slot list (`[(0, info_new)]`), which does not match `(0, info_old)`, so it correctly does **not** clear dirty — but the disk has already been corrupted with the stale pointer.
4. `remove_old_stores_shrink` (called by `shrink_storage`) drops the old storage `A` (`Arc<AccountStorageEntry>`) via `mark_dirty_dead_stores`, freeing/reusing that append-vec.
5. At this point the in-memory entry is still correct (`info_new`, dirty=true since step 3 left it dirty), so reads through `get_only_in_mem`/`get_internal_inner` are unaffected — the in-mem map is always consulted before falling back to disk. But if this entry is later evicted while clean, or reloaded after the process restarts (disk index is meant to survive eviction/restart), a subsequent `load_account_entry_from_disk`/`load_from_disk` will return the **stale** `(0, info_old)` pointing at storage `A`, which has since been dropped/reused, producing a dangling storage reference.

The pre-existing single-ref/ref-count/dirty guards on `write_through` prevent multi-version entries from being persisted incorrectly, but they do **not** make the disk write itself atomic with the in-memory state check — the disk `write_to_disk` call happens first, unconditionally, and the correctness check only gates the dirty-flag clearing, not the write.

### Impact Explanation
This is a stale/wrong-version account-data read hazard: the on-disk secondary index (used when `IndexLimit::Threshold` disk-backed indexing is enabled) can be left holding an `AccountInfo` referencing a storage entry (`AppendVec`) that has been dropped by `shrink_storage`. If that entry is later reloaded from disk (post-eviction or post-restart) instead of being served from the in-memory map, `AccountsDb::load`/`do_load_for_tests` would attempt to read from a freed/reused storage location, matching the "read of freed/invalid storage location producing corrupted or zeroed account data" impact category.

### Likelihood Explanation
This requires: (a) the validator running with disk-index write-through enabled (`IndexLimit::Threshold`, an operator-configurable setting, not the default `InMemOnly`), (b) an account cached at both a to-be-rooted slot and a concurrently-abandoned unrooted slot for the same pubkey (achievable by an attacker forking transactions across slots as described), and (c) a narrow but real race window between `remove_unrooted_slots`'s cache purge and a background `shrink_storage` pass on the just-rooted slot. Because write-through/disk-index mode is an opt-in operator configuration rather than the default production path, and the corrupted in-memory state self-heals on the next mutation (dirty flag correctly remains set in the observed ordering), the practical exploitability for an unprivileged attacker without control over validator config or precise thread scheduling is low; it manifests only under specific non-default configuration and specific internal scheduling that the attacker cannot directly control (a background shrink pass timing relative to bank-drop processing).

### Recommendation
Make `write_through()` atomic with respect to the in-memory check: perform the disk write only while holding the same lock/guard used to validate the slot-list/ref-count match (or re-validate immediately before writing under a lock that also blocks concurrent `replace()`/`upsert()` write-throughs for the same pubkey), so a stale value can never be written to disk after a newer value already landed there. Alternatively, serialize `write_through` calls per-pubkey with a monotonic version/sequence check so an older write is rejected if a newer write-through has already completed.

### Proof of Concept
Concurrency/invariant test plan (extend `accounts-db/src/accounts_index/in_mem_accounts_index.rs` test module, using `new_should_write_through_for_test`):
```rust
#[test]
fn test_write_through_race_with_replace_does_not_leave_stale_disk_entry() {
    let index = new_should_write_through_for_test(None);
    let pubkey = solana_pubkey::new_rand();
    let old_info = 10u64;
    let new_info = 20u64;

    // Seed entry: single-ref, dirty, slot_list = [(0, old_info)]
    let new_value = PreAllocatedAccountMapEntry::new(0, old_info, &index.storage, true);
    index.upsert(&pubkey, new_value, None, &mut ReclaimsSlotList::new(), UpsertReclaim::IgnoreReclaims);

    // Simulate interleaving: capture "old" state as try_write_through would,
    // then run replace() (simulating shrink) before the captured write_through fires.
    let captured = (0u64, old_info);
    index.replace(&pubkey, (0, new_info), 0); // simulates shrink's update_index_for_shrink

    // Now fire the stale write_through using the captured (stale) value,
    // simulating remove_unrooted_slots's deferred write.
    index.write_through(&pubkey, captured.0, captured.1);

    // Assert: disk must reflect the latest (new_info), never the stale old_info.
    let (slot_list, _) = index.load_from_disk(&pubkey).expect("entry should be on disk");
    assert_eq!(
        slot_list, SlotList::from([(0, new_info)]),
        "disk must not regress to a stale AccountInfo after a newer write_through committed"
    );
}
```
Expected (bug present): assertion fails — disk contains `[(0, old_info)]` instead of `[(0, new_info)]`, demonstrating the stale write. A stronger integration-level PoC would follow the exact `store_for_tests(0,P) -> store_for_tests(1,P) -> add_root_and_flush_write_cache(0) -> shrink_storage(0) || remove_unrooted_slots([(1,bank_id)])` sequence under a `Threshold` index config with repeated randomized thread scheduling (loom-style), asserting that `load_from_disk(&P)` after both operations complete never returns an `AccountInfo` referencing a storage id that `remove_old_stores_shrink` has already dropped.

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L464-481)
```rust
    fn write_through(&self, pubkey: &Pubkey, slot: Slot, account_info: T) {
        let disk = self.bucket.as_ref().unwrap();
        let disk_entry = [(slot, account_info.into())];
        let grow_us = Self::write_to_disk(disk, pubkey, &disk_entry);
        Self::update_stat(&self.stats().flush_entries_updated_on_disk_immediate, 1);
        Self::update_stat(&self.stats().flush_grow_us, grow_us);
        self.get_only_in_mem(pubkey, false, |entry| {
            if let Some(entry) = entry {
                let slot_list = entry.slot_list_read_lock();
                if slot_list.len() == 1
                    && slot_list[0] == (slot, account_info)
                    && entry.ref_count() == 1
                {
                    entry.clear_dirty();
                }
            }
        });
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L483-502)
```rust
    /// If the in-mem entry for pubkey is `slot_list.len() == 1` with `ref_count == 1` and
    /// currently dirty, write it through to disk
    pub fn try_write_through(&self, pubkey: &Pubkey) {
        let to_write = self.get_only_in_mem(pubkey, false, |entry| {
            entry.and_then(|entry| {
                if !entry.dirty() {
                    return None;
                }

                let slot_list = entry.slot_list_read_lock();
                match (entry.ref_count(), &slot_list[..]) {
                    (1, [info]) => Some(*info),
                    _ => None,
                }
            })
        });
        if let Some((slot, info)) = to_write {
            self.write_through(pubkey, slot, info);
        }
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L568-602)
```rust
    /// Replaces the slot list entry at `old_slot` with `new_item`.
    ///
    /// Panics if `old_slot` is not present in the slot list, or if more than one entry at
    /// `old_slot` is found (which would indicate prior corruption).
    pub fn replace(&self, pubkey: &Pubkey, new_item: SlotListItem<T>, old_slot: Slot) {
        let mut should_write_through = false;

        self.get_or_create_index_entry_for_pubkey(pubkey, |entry| {
            let mut slot_list = entry.slot_list_write_lock();
            let mut found_slot = false;
            let slot_list_length = slot_list.retain_and_count(|cur_item| {
                if cur_item.0 == old_slot {
                    assert!(
                        !found_slot,
                        "duplicate entry at slot {old_slot} in slot_list"
                    );
                    found_slot = true;
                    *cur_item = new_item;
                }
                true
            });
            assert!(
                found_slot,
                "Expected to find a slot to replace in the slot list"
            );
            entry.mark_dirty();

            should_write_through =
                self.should_write_through && slot_list_length == 1 && entry.ref_count() == 1;
        });
        if should_write_through {
            let (slot, account_info) = new_item;
            self.write_through(pubkey, slot, account_info);
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L4957-5001)
```rust
    /// Updates the accounts index for the shrink path: each account at `accounts.slot(i)` has
    /// its existing index entry replaced to point at the rewritten storage at `target_slot`.
    ///
    /// Unlike `update_index_stored_accounts` this does not collect reclaims — the caller is
    /// responsible for the source storage's alive-bytes accounting. Secondary indexes are also
    /// not touched, since shrink only changes `(store_id, offset)` and they index by pubkey.
    fn update_index_for_shrink<'a>(
        &self,
        infos: &[AccountInfo],
        accounts: &impl StorableAccounts<'a>,
        update_index_thread_selection: UpdateIndexThreadSelection,
        thread_pool: &ThreadPool,
    ) {
        let target_slot = accounts.target_slot();
        let len = std::cmp::min(accounts.len(), infos.len());

        let update = |start, end| {
            (start..end).for_each(|i| {
                let info: AccountInfo = infos[i];
                let old_slot = accounts.slot(i);
                let pubkey = accounts.pubkey(i);
                self.accounts_index
                    .replace(target_slot, old_slot, pubkey, info);
            });
        };

        let threshold = 1;
        if matches!(
            update_index_thread_selection,
            UpdateIndexThreadSelection::PoolWithThreshold,
        ) && len > threshold
        {
            let chunk_size = len.div_ceil(thread_pool.current_num_threads());
            let batches = 1 + len / chunk_size;
            thread_pool.install(|| {
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
```
