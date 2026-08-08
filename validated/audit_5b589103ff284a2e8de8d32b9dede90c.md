Confirmed: there is no shrink path for the disk-backed bucket map's index or data buckets. `grow_index` and `grow_data` in [1](#0-0)  and [2](#0-1)  only ever increase capacity (`current_capacity * 110 / 100` or doubling `current_capacity_pow2 + 1`), and `delete_key` at [3](#0-2)  only frees the slot in-place — it never triggers a resize down. A search for "shrink" in the `bucket_map` crate returns nothing.

### Title
Unprivileged accounts can permanently inflate the on-disk accounts-index bucket map, causing disproportionate, irreversible storage/CPU cost - (File: bucket_map/src/bucket.rs)

### Summary
Any unprivileged user who creates a large, temporary burst of accounts (e.g., short-lived, rent-exempt accounts subsequently closed) forces the accounts-index `BucketMap`'s on-disk index and data buckets to grow via `grow_index`/`grow_data`. Because the bucket map has no shrink mechanism, this capacity is never reclaimed for the lifetime of the validator process, permanently increasing disk, mmap, and background-flush/scan CPU overhead disproportionate to the actual number of live accounts — conceptually analogous to the C4 "Basket" report where a single actor's action locks in an inflated, unrecoverable state that everyone else pays for.

### Finding Description
The accounts index's disk-backed storage (`BucketMap`, used by `InMemAccountsIndex`/`BucketMapHolder`) allocates per-bin `Bucket<T>` structures that hold an index bucket (`BucketStorage<IndexBucket<T>>`) and one or more data buckets (`BucketStorage<DataBucket>`). When a bin fills up during inserts (`try_write` failing with `BucketMapError::IndexNoSpace`/`DataNoSpace`), `Bucket::grow` is invoked:

- `grow_index` reallocates a strictly larger index (`new_capacity = (current_capacity * 110 / 100).max(anticipated_size)`) and copies all existing entries into it: [1](#0-0) 
- `grow_data` reallocates a data bucket at a strictly larger power-of-two capacity: [2](#0-1) 

`delete_key` only frees the slot inside the existing (already-grown) storage; it never shrinks or replaces the underlying `BucketStorage` allocation: [3](#0-2) 

There is no corresponding "shrink_index"/"shrink_data" routine anywhere in the `bucket_map` crate (confirmed via search — zero matches for "shrink"). Once a per-bin bucket grows to accommodate a burst of temporary accounts, that mmap'd file (and its associated memory/disk footprint) persists at the inflated size for the life of the process, even after all the accounts that caused the growth are closed and the entries removed via `delete_key`. Because `bucket_index_ix` hashes each pubkey into one of a fixed number of bins using a process-local random seed [4](#0-3) , a user does not even need to target a specific bin — any large, sustained burst of account creation (fully affordable via normal rent-exempt account creation, no special privilege required) will inflate one or more bins permanently.

### Impact Explanation
This matches the accepted "disproportionate storage and CPU cost" impact category. A single unprivileged user, at the cost of the rent-exempt minimum for however many temporary accounts they create (recoverable by later closing the accounts), can force a permanent, unrecoverable increase in the validator's index storage footprint on every validator that processes those transactions — since the accounts-index bin capacity growth happens deterministically as accounts are inserted across the cluster. This increases per-validator disk usage, mmap overhead, and the CPU cost of every subsequent grow/copy, flush, and index-scan operation that iterates the now-oversized bucket, without a bound tied to the actual number of live accounts. Unlike the AppendVec-level accounts storage (which has `shrink_storage`/`clean_accounts`/`shrink_candidate_slots` to reclaim space, per [5](#0-4) ), the disk index bucket map has no analogous reclamation path.

### Likelihood Explanation
High. No special privilege, timing, or coordination is required — any user or set of users can create enough temporary accounts (a common, cheap operation) to drive up bucket capacities via ordinary rent-exempt account creation and subsequent closure. This is a low-cost, repeatable, purely economic attack surface requiring only ordinary transaction submission.

### Recommendation
Add a shrink/compaction path for `Bucket`'s index and data `BucketStorage` (mirroring the existing AppendVec `shrink_storage`/`clean_accounts` machinery), triggered when the occupied-to-capacity ratio for a bin falls below a threshold, so that on-disk index capacity can track the live account population rather than only ever growing.

### Proof of Concept
1. Bring up a validator and observe the per-bin capacity of the disk-backed accounts index (`BucketMapStats`, `max_size`/`total_file_size` in [6](#0-5) ).
2. As an ordinary account owner, submit transactions creating a very large number of small, rent-exempt accounts in a burst (spread across pubkeys so they land in the same or a small number of index bins, or simply enough in aggregate to trigger multiple `grow_index`/`grow_data` calls).
3. Close/delete all of those accounts (`delete_key` reclaims the in-bucket slot only).
4. Observe that `BucketStats::total_file_size`/`max_size` for the affected bin(s) never decreases — the mmap'd index/data files remain at their peak size for the remainder of the process lifetime, even though the number of live entries has returned to (near) zero.

### Citations

**File:** bucket_map/src/bucket.rs (L663-679)
```rust
    pub fn delete_key(&mut self, key: &Pubkey) {
        if let Some((elem, elem_ix)) = self.find_index_entry(key) {
            self.at_least_one_entry_deleted = true;
            if let OccupiedEnum::MultipleSlots(multiple_slots) =
                elem.get_slot_count_enum(&self.index)
            {
                let ix = multiple_slots.data_bucket_ix() as usize;
                let data_bucket = &self.data[ix];
                let loc = multiple_slots.data_loc(data_bucket);
                let data_bucket = &mut self.data[ix];
                //debug!(                    "DATA FREE {:?} {} {} {}",                    key, elem.data_location, data_bucket.capacity, elem_uid                );
                data_bucket.free(loc);
            }
            //debug!("INDEX FREE {:?} {}", key, elem_uid);
            self.index.free(elem_ix);
        }
    }
```

**File:** bucket_map/src/bucket.rs (L685-747)
```rust
    pub fn grow_index(&self, mut current_capacity: u64) {
        if self.index.contents.capacity() == current_capacity {
            // make sure to grow to at least % more than the anticipated size
            // The indexing algorithm expects to require some over-allocation.
            let anticipated_size = self.anticipated_size * 140 / 100;
            let mut m = Measure::start("grow_index");
            //debug!("GROW_INDEX: {}", current_capacity_pow2);
            let mut count = 0;
            loop {
                count += 1;
                // grow relative to the current capacity
                let new_capacity = (current_capacity * 110 / 100).max(anticipated_size);
                let (mut index, file_name) = BucketStorage::new_with_capacity(
                    Arc::clone(&self.drives),
                    1,
                    std::mem::size_of::<IndexEntry<T>>() as u64,
                    Capacity::Actual(new_capacity),
                    self.index.max_search,
                    Arc::clone(&self.stats.index),
                    Arc::clone(&self.index.count),
                );
                // index may have allocated something larger than we asked for,
                // so, in case we fail to reindex into this larger size, grow from this size next iteration.
                current_capacity = index.capacity();
                let mut valid = true;
                for ix in 0..self.index.capacity() {
                    if !self.index.is_free(ix) {
                        let elem: &IndexEntry<T> = self.index.get(ix);
                        let new_ix =
                            Self::bucket_create_key(&mut index, &elem.key, self.random, true);
                        if new_ix.is_err() {
                            valid = false;
                            break;
                        }
                        let new_ix = new_ix.unwrap();
                        let new_elem: &mut IndexEntry<T> = index.get_mut(new_ix);
                        *new_elem = *elem;
                        index.copying_entry(new_ix, &self.index, ix);
                    }
                }
                if valid {
                    self.stats.index.update_max_size(index.capacity());
                    let mut items = self.reallocated.items.lock().unwrap();
                    items.index = Some(index);
                    self.reallocated.add_reallocation();
                    self.restartable_bucket.set_file(file_name, self.random);
                    break;
                }
            }
            m.stop();
            if count > 1 {
                self.stats
                    .index
                    .failed_resizes
                    .fetch_add(count - 1, Ordering::Relaxed);
            }
            self.stats.index.resizes.fetch_add(1, Ordering::Relaxed);
            self.stats
                .index
                .resize_us
                .fetch_add(m.as_us(), Ordering::Relaxed);
        }
    }
```

**File:** bucket_map/src/bucket.rs (L804-820)
```rust
    pub fn grow_data(&self, data_index: u64, current_capacity_pow2: u8) {
        let (new_bucket, _file_name) = BucketStorage::new_resized(
            &self.drives,
            self.index.max_search,
            self.data.get(data_index as usize),
            Capacity::Pow2(std::cmp::max(
                current_capacity_pow2 + 1,
                DEFAULT_CAPACITY_POW2,
            )),
            1 << data_index,
            Self::elem_size(),
            &self.stats.data,
        );
        self.reallocated.add_reallocation();
        let mut items = self.reallocated.items.lock().unwrap();
        items.data = Some((data_index, new_bucket));
    }
```

**File:** bucket_map/src/bucket.rs (L822-828)
```rust
    fn bucket_index_ix(key: &Pubkey, random: u64) -> u64 {
        // the locally generated random will make it hard for an attacker
        // to deterministically cause all the pubkeys to land in the same
        // location in any bucket on all validators
        let hasher_builder = ahash::RandomState::with_seeds(random, random, random, random);
        hasher_builder.hash_one(key)
    }
```

**File:** accounts-db/src/accounts_db.rs (L2781-2802)
```rust
    /// Shrinks `store` by rewriting the alive accounts to a new storage
    fn shrink_storage(&self, store: Arc<AccountStorageEntry>) {
        let slot = store.slot();
        if self.accounts_cache.contains(slot) {
            // It is not correct to shrink a slot while it is in the write cache until flush is complete and the slot is removed from the write cache.
            // There can exist a window after a slot is made a root and before the write cache flushing for that slot begins and then completes.
            // There can also exist a window after a slot is being flushed from the write cache until the index is updated and the slot is removed from the write cache.
            // During the second window, once an append vec has been created for the slot, it could be possible to try to shrink that slot.
            // Shrink no-ops before this function if there is no store for the slot (notice this function requires 'store' to be passed).
            // So, if we enter this function but the slot is still in the write cache, reasonable behavior is to skip shrinking this slot.
            // Flush will ONLY write alive accounts to the append vec, which is what shrink does anyway.
            // Flush then adds the slot to 'uncleaned_roots', which causes clean to take a look at the slot.
            // Clean causes us to mark accounts as dead, which causes shrink to later take a look at the slot.
            // This could be an assert, but it could lead to intermittency in tests.
            // It is 'correct' to ignore calls to shrink when a slot is still in the write cache.
            return;
        }
        let mut unique_accounts =
            self.get_unique_accounts_from_storage_for_shrink(&store, &self.shrink_stats);
        debug!("do_shrink_slot_store: slot: {slot}");
        let shrink_collect = self.shrink_collect::<AliveAccounts<'_>>(
            &store,
```

**File:** bucket_map/src/bucket_stats.rs (L12-37)
```rust
#[derive(Debug, Default)]
pub struct BucketStats {
    pub resizes: AtomicU64,
    pub failed_resizes: AtomicU64,
    pub max_size: AtomicU64,
    pub resize_us: AtomicU64,
    pub new_file_us: AtomicU64,
    pub mmap_us: AtomicU64,
    pub find_index_entry_mut_us: AtomicU64,
    pub file_count: AtomicU64,
    pub total_file_size: AtomicU64,
    pub startup: StartupBucketStats,
    pub index_uses_uncommon_slot_list_len_or_refcount: AtomicBool,
}

impl BucketStats {
    pub fn update_max_size(&self, size: u64) {
        self.max_size.fetch_max(size, Ordering::Relaxed);
    }

    pub fn resize_grow(&self, old_size: u64, new_size: u64) {
        let size_change = new_size.saturating_sub(old_size);
        self.total_file_size
            .fetch_add(size_change, Ordering::Relaxed);
    }
}
```
