Based on my research, I found a legitimate analog within the permitted scope (bucket map): freed disk-bucket slots and once-grown data-bucket allocations are never reclaimed or defragmented, and the crate exposes no compaction/sweep function to recover the wasted disk capacity.

### Title
Disk-backed bucket map data buckets never shrink or defragment after entries are freed, causing permanent disproportionate disk usage with no reclaim path - ([File: bucket_map/src/bucket.rs], [File: bucket_map/src/bucket_storage.rs])

### Summary
The external report describes reward tokens becoming permanently stuck due to integer-division precision loss with no admin function to sweep/recover them. The closest analog in agave's in-scope surfaces (accounts index disk bucket map) is that `Bucket::grow_data` [1](#0-0)  and `Bucket::grow_index` [2](#0-1)  only ever allocate larger backing files; there is no corresponding path that shrinks a data or index `BucketStorage` back down after `free`/`delete_key` removes entries [3](#0-2) [4](#0-3) .

### Finding Description
`Bucket::try_write` selects a "best fit" data bucket sized as the next power-of-two `>= num_slots` via `MultipleSlots::data_bucket_from_num_slots` [5](#0-4) . When an account's slot list grows (e.g., many refs/slots), `try_write` moves the entry into the larger-capacity data bucket and frees the old slot [6](#0-5) . If that data later shrinks back down (fewer slots) or the key is deleted, `delete_key` only calls `BucketStorage::free` on the mmap-backed cell, decrementing `count` but not the file's `capacity()` [4](#0-3) [3](#0-2) . The backing mmap file for that data-bucket "power" is never shrunk, replaced with a smaller file, or defragmented — `grow_data`/`apply_grow_data` are strictly one-directional growth operations [7](#0-6) . Likewise, the index bucket's `grow_index` rebuilds into ever-larger files with no shrink counterpart [8](#0-7) . Unlike the in-memory hashmap path, which explicitly added `reallocate_to_clear_tombstones` to rebuild bins and reclaim capacity lost to tombstones [9](#0-8) , the on-disk `bucket_map` crate has no equivalent compaction/sweep routine for its `BucketStorage` files. Once a bucket's capacity is grown (whether due to a transient burst of index insertions or a single large slot-list entry), that disk allocation is permanently retained for the life of the process, and there is no admin/maintenance API to reclaim it.

### Impact Explanation
This causes disproportionate and unrecoverable disk storage consumption: a validator that experiences a temporary spike in index density (bulk load, restart-time population, or a transient set of accounts with large ref counts/slot lists) will have its accounts-index disk buckets permanently sized to that peak, even after the underlying entries are deleted and `count` drops back down. There is no mechanism analogous to a "sweep" function to shrink these files back to the sizes actually needed, unlike `AccountsDb`'s explicit shrink/clean/purge machinery for account storages (`shrink_candidate_slots`, `clean_accounts`, ancient-storage packing) [10](#0-9) . Over the life of a long-running validator this can lead to steadily growing, never-reclaimed disk usage in the accounts-index bucket map, which matches the "disproportionate storage cost" impact category.

### Likelihood Explanation
Likelihood is moderate: any workload that momentarily increases the size of index bins (large batch account creation, restart-time index generation, or accounts acquiring temporarily large ref counts/slot lists) will trigger `grow_index`/`grow_data`, and normal deletion/pruning traffic afterward will free entries via `Bucket::free`/`delete_key` without ever returning the allocated capacity. This requires no attacker action — it is a structural gap reachable through ordinary index churn.

### Recommendation
Add a compaction/defragmentation path for `BucketStorage`, analogous to `in_mem_accounts_index::reallocate_to_clear_tombstones`, that periodically (or under low free-entry/utilization conditions) rebuilds an oversized index or data bucket into a new, appropriately smaller mmap file when the ratio of `count()`/`capacity()` drops below a threshold, then swaps it in the same way `apply_grow_index`/`apply_grow_data` swap in enlarged buckets.

### Proof of Concept
Not directly executable from the index alone; a conceptual repro is: (1) insert a burst of pubkeys sufficient to trigger `grow_index`/`grow_data` to a large capacity via `Bucket::try_write`/`insert` [11](#0-10) ; (2) delete the majority of those pubkeys via `delete_key`, which frees entries and decrements `count` but never `capacity()` [4](#0-3) [3](#0-2) ; (3) observe that `BucketStorage::capacity()`/file size for both the index and data buckets remains at the peak value indefinitely, with no API call available to shrink it back down, unlike the tombstone-triggered reallocation available for the in-memory hashmap path.

### Citations

**File:** bucket_map/src/bucket.rs (L592-660)
```rust
        // need to move the allocation to a best fit spot
        let best_bucket = &mut self.data[best_fit_bucket as usize];
        let cap_power = best_bucket.contents.capacity_pow2();
        let cap = best_bucket.capacity();
        let pos = rng().random_range(0..cap);
        let mut success = false;
        // max search is increased here by a lot for this search. The idea is that we just have to find an empty bucket somewhere.
        // We don't mind waiting on a new write (by searching longer). Writing is done in the background only.
        // Wasting space by doubling the bucket size is worse behavior. We expect more
        // updates and fewer inserts, so we optimize for more compact data.
        // We can accomplish this by increasing how many locations we're willing to search for an empty data cell.
        // For the index bucket, it is more like a hash table and we have to exhaustively search 'max_search' to prove an item does not exist.
        // And we do have to support the 'does not exist' case with good performance. So, it makes sense to grow the index bucket when it is too large.
        // For data buckets, the offset is stored in the index, so it is directly looked up. So, the only search is on INSERT or update to a new sized value.
        for i in pos..pos + (max_search * 10).min(cap) {
            let ix = i % cap;
            if best_bucket.is_free(ix) {
                let mut multiple_slots = MultipleSlots::default();
                multiple_slots.set_storage_offset(ix);
                multiple_slots
                    .set_storage_capacity_when_created_pow2(best_bucket.contents.capacity_pow2());
                multiple_slots.set_num_slots(num_slots);
                MultipleSlots::set_ref_count(best_bucket, ix, ref_count);

                //debug!(                        "DATA ALLOC {:?} {} {} {}",                        key, elem.data_location, best_bucket.capacity, elem_uid                    );
                let best_bucket = &mut self.data[best_fit_bucket as usize];
                best_bucket.occupy(ix, false).unwrap();
                if num_slots > 0 {
                    // copy slotlist into the data bucket
                    let slice = best_bucket.get_slice_mut(ix, num_slots, IncludeHeader::NoHeader);
                    slice.iter_mut().zip(data).for_each(|(dest, src)| {
                        *dest = *src;
                    });
                }

                // update index bucket after data bucket has been updated.
                elem.unwrap_or_else(|| {
                    let is_resizing = false;
                    self.index.occupy(elem_ix, is_resizing).unwrap();
                    let elem_allocate = IndexEntryPlaceInBucket::new(elem_ix);
                    // These fields will be overwritten after allocation by callers.
                    // Since this part of the mmapped file could have previously been used by someone else, there can be garbage here.
                    elem_allocate.init(&mut self.index, key);
                    elem_allocate
                })
                .set_slot_count_enum_value(
                    &mut self.index,
                    OccupiedEnum::MultipleSlots(&multiple_slots),
                );
                self.stats
                    .index
                    .index_uses_uncommon_slot_list_len_or_refcount
                    .store(true, Ordering::Relaxed);
                success = true;
                break;
            }
        }
        if !success {
            return Err(BucketMapError::DataNoSpace((best_fit_bucket, cap_power)));
        }
        if let Some(DataFileEntryToFree {
            bucket_ix,
            location,
        }) = old_data_entry_to_free
        {
            // free the entry in the data bucket the data was previously stored in
            self.data[bucket_ix].free(location);
        }
        Ok(())
```

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

**File:** bucket_map/src/bucket.rs (L770-820)
```rust
    fn add_data_bucket(&mut self, bucket: BucketStorage<DataBucket>) {
        self.stats.data.file_count.fetch_add(1, Ordering::Relaxed);
        self.stats.data.resize_grow(0, bucket.capacity_bytes());
        self.data.push(bucket);
    }

    pub fn apply_grow_data(&mut self, ix: usize, bucket: BucketStorage<DataBucket>) {
        if self.data.get(ix).is_none() {
            for i in self.data.len()..ix {
                // insert empty data buckets
                self.add_data_bucket(
                    BucketStorage::new(
                        Arc::clone(&self.drives),
                        1 << i,
                        Self::elem_size(),
                        self.index.max_search,
                        Arc::clone(&self.stats.data),
                        Arc::default(),
                    )
                    .0,
                );
            }
            self.add_data_bucket(bucket);
        } else {
            let data_bucket = &mut self.data[ix];
            self.stats
                .data
                .resize_grow(data_bucket.capacity_bytes(), bucket.capacity_bytes());
            self.data[ix] = bucket;
        }
    }

    /// grow a data bucket
    /// The application of the new bucket is deferred until the next write lock.
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

**File:** bucket_map/src/bucket.rs (L861-870)
```rust
    pub fn insert(&mut self, key: &Pubkey, value: (&[T], RefCount)) {
        let (new, refct) = value;
        loop {
            let Err(err) = self.try_write(key, new.iter(), new.len(), refct) else {
                return;
            };
            self.grow(err);
            self.handle_delayed_grows();
        }
    }
```

**File:** bucket_map/src/bucket_storage.rs (L301-306)
```rust
    pub fn free(&mut self, ix: u64) {
        debug_assert!(ix < self.capacity(), "bad index size");
        let start = self.get_start_offset_with_header(ix);
        self.contents.free(&mut self.mmap[start..], ix as usize);
        self.count.fetch_sub(1, Ordering::Relaxed);
    }
```

**File:** bucket_map/src/index_entry.rs (L253-269)
```rust
    pub(crate) fn data_bucket_ix(&self) -> u64 {
        Self::data_bucket_from_num_slots(self.num_slots())
    }

    /// return closest bucket index fit for the slot slice.
    /// Since bucket size is 2^index, the return value is
    ///     min index, such that 2^index >= num_slots
    ///     index = ceiling(log2(num_slots))
    /// special case, when slot slice empty, return 0th index.
    pub(crate) fn data_bucket_from_num_slots(num_slots: Slot) -> u64 {
        // Compute the ceiling of log2 for integer
        if num_slots == 0 {
            0
        } else {
            (Slot::BITS - (num_slots - 1).leading_zeros()) as u64
        }
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1457-1491)
```rust
    /// Rebuild the bin's HashMap into a fresh allocation to clear tombstones left
    /// behind by evictions. hashbrown counts tombstones against `capacity`, so
    /// without this the bin's effective capacity drifts down over time and triggers
    /// the hashmap to double in capacity.
    ///
    /// Only called in Threshold mode, where `capacity >= target_entries` is guaranteed
    /// by the time eviction runs (`check_flush_trigger` gates on `high_water_mark`).
    fn reallocate_to_clear_tombstones(&self) {
        let stats = self.stats();
        let m = Measure::start("reallocate_hashmap");

        let target_entries = self
            .storage
            .threshold_entries_per_bin
            .as_ref()
            .expect("reallocate_to_clear_tombstones only runs in Threshold mode")
            .target_entries;

        let mut map = self.map_internal.write().unwrap();
        let capacity_pre = map.capacity();

        // Drain the old map into a fresh allocation sized to `target_entries` so the
        // backing storage stays stable across eviction cycles. Building a brand-new
        // map (rather than `shrink_to_fit`) guarantees a full rehash, which is what
        // actually clears the tombstones.
        let mut new_map = HashMap::with_capacity_and_hasher(target_entries, map.hasher().clone());
        new_map.extend(map.drain());
        *map = new_map;
        let capacity_post = map.capacity();
        drop(map);

        stats.update_in_mem_capacity(capacity_pre, capacity_post);
        Self::update_stat(&stats.num_hashmap_reallocates, 1);
        Self::update_time_stat(&stats.hashmap_reallocate_us, m);
    }
```

**File:** runtime/src/accounts_background_service.rs (L559-571)
```rust
                            let duration_since_previous_shrink = previous_shrink_time.elapsed();
                            let should_shrink = duration_since_previous_shrink > SHRINK_INTERVAL;
                            // To avoid pathological interactions between the clean and shrink
                            // timers, call shrink for either should_shrink or should_clean.
                            if should_shrink || should_clean {
                                if should_clean {
                                    // We used to only squash (aka shrink ancients) when we also
                                    // cleaned, so keep that same behavior here for now.
                                    bank.shrink_ancient_slots();
                                }
                                bank.shrink_candidate_slots();
                                previous_shrink_time = Instant::now();
                            }
```
