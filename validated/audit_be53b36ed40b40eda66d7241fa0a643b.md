## Analysis Summary

I found a valid analog to the reported bug class in `agave`'s bucket-map / disk-index growth path, where concurrent "grow" requests for the same on-disk bucket race on a shared single-slot state, leading to a validator panic — an accepted impact per the rules.

### Title
Concurrent bucket-map index/data reallocation requests overwrite the shared `Reallocated` slot and trigger a fatal assertion panic - (File: `bucket_map/src/bucket.rs`)

### Summary
`Bucket::grow_index`/`grow_data` in `bucket_map/src/bucket.rs` build a new resized `BucketStorage` and stash it in a single shared `Reallocated` struct to be applied later on the next write-lock acquisition. Because `grow()` is invoked while holding only a *read* lock on the bucket [1](#0-0) , multiple threads can concurrently trigger a grow for the same bucket. The result is stored via a `Mutex`-guarded write to `items.index`/`items.data`, immediately followed by `add_reallocation()`, which asserts that only one reallocation may be pending at a time [2](#0-1) . A second concurrent grow silently overwrites the first thread's freshly-built bucket in `items` and then fails the `assert_eq!(0, ...)` check, crashing the process.

### Finding Description
All *mutating* bucket operations (`insert`, `update`, `try_write`, `batch_insert_non_duplicates`, `delete_key`, `set_anticipated_count`) take an exclusive write lock via `get_write_bucket()` [3](#0-2) . However, `grow()` is deliberately special-cased to use only a **read** lock so that a caller who received a `BucketMapError` from `try_write` can trigger a resize without blocking other readers:

```rust
pub fn grow(&self, err: BucketMapError) {
    // grows are special - they get a read lock and modify 'reallocated'
    // the grown changes are applied the next time there is a write lock taken
    if let Some(bucket) = self.bucket.read().unwrap().as_ref() {
        bucket.grow(err)
    }
}
``` [1](#0-0) 

`RwLock::read()` permits multiple concurrent readers, so `grow_index`/`grow_data` can run **concurrently** in different threads for the same `Bucket`:

```rust
pub fn grow_index(&self, mut current_capacity: u64) {
    if self.index.contents.capacity() == current_capacity {
        ...
        if valid {
            self.stats.index.update_max_size(index.capacity());
            let mut items = self.reallocated.items.lock().unwrap();
            items.index = Some(index);
            self.reallocated.add_reallocation();
            self.restartable_bucket.set_file(file_name, self.random);
            break;
        }
    }
}
``` [4](#0-3) 

```rust
pub fn add_reallocation(&self) {
    assert_eq!(
        0,
        self.active_reallocations.fetch_add(1, Ordering::Relaxed),
        "Only 1 reallocation can occur at a time"
    );
}
``` [5](#0-4) 

Because `Reallocated::items` only has a single `index: Option<BucketStorage<I>>` slot (and a single `data` slot), and `active_reallocations` is a single shared counter, two threads that both hit `IndexNoSpace` (or `DataNoSpace`) for the same bucket at roughly the same time will:
1. Thread A locks `items`, sets `items.index = Some(A)`, calls `add_reallocation()` (0→1, succeeds), unlocks.
2. Thread B locks `items`, **overwrites** `items.index = Some(B)` (discarding A's grown bucket), then calls `add_reallocation()`, which sees `active_reallocations == 1` and fails `assert_eq!(0, 1, "Only 1 reallocation can occur at a time")`, panicking the thread.

This mirrors the external report's bug class exactly: a single shared "last write wins" storage slot that is meant to hold state for one in-flight request, but is reachable by multiple concurrent producers, causing the second one to clobber the first.

Reachability: every disk-bucket write path (`BucketApi::insert`/`try_write`/`update`/`batch_insert_non_duplicates`) can independently call `.grow()` after receiving a `BucketMapError::IndexNoSpace`/`DataNoSpace` from `Bucket::try_write` [6](#0-5) . In the accounts index, this bucket is the on-disk backing store for `InMemAccountsIndex`, reached both synchronously (e.g. `try_write_through`/`write_to_disk` during account-cache flush) and asynchronously by the dedicated `solIdxFlusher` background threads [7](#0-6) , and by `write_to_disk`'s own grow-retry loop [8](#0-7) . If two such call paths hit capacity exhaustion for the same bucket concurrently (a realistic scenario under heavy insert/update load causing simultaneous `IndexNoSpace`/`DataNoSpace` from independent writers targeting different keys in the same bucket), the described race is triggered.

### Impact Explanation
The immediate, deterministic effect of the race is a hard `assert_eq!` panic in the accounts-index/bucket-map subsystem, which aborts the validator process — a "node panic," one of the impacts explicitly accepted by the validation rules. Beyond the panic, the overwritten reallocation (the discarded index/data bucket built by the losing thread) represents wasted/duplicated CPU and memory work under load (disproportionate CPU cost), since a full bucket resize (rehashing all existing entries) is thrown away and must eventually be redone.

### Likelihood Explanation
The race requires two concurrent callers to both encounter a resize-triggering error (`BucketMapError::IndexNoSpace`/`DataNoSpace`) on the *same* bucket within the narrow window between one thread's read-lock-protected `grow()` call and the next write-lock's `handle_delayed_grows()`. Because bucket assignment is based on a pubkey-derived hash across a bounded number of buckets (`BucketMap::bucket_ix`) [9](#0-8) , and because both the background flusher thread and any synchronous write-through/flush path can touch the same bucket independently, this is plausible under sustained write load and/or when the accounts index disk bucket capacity is near its threshold — not a purely theoretical race, but it does require specific timing under load rather than being trivially triggerable on demand.

### Recommendation
Make the reallocation-state slot per-reallocation-kind and safe for concurrent producers instead of a single overwritable value guarded by an assert:
- Store a `Vec`/queue of pending reallocations (or one bounded slot **per kind**, e.g. keyed by whether it's an index or specific data-bucket index) so multiple concurrently-computed resizes cannot silently clobber one another.
- Replace the `assert_eq!` panic-on-race with either (a) proper serialization of `grow()` calls per bucket (e.g., a dedicated grow-lock instead of relying on the RwLock read-lock trick), or (b) idempotent/mergeable handling that detects an in-flight reallocation of the same kind and safely discards the redundant one without crashing the process.
- Add a regression test that spawns multiple threads concurrently forcing `IndexNoSpace`/`DataNoSpace` on the same `Bucket` to verify no panic occurs and no reallocation is lost.

### Proof of Concept
Conceptual PoC (based on `bucket_map/src/bucket.rs` and `bucket_api.rs`):
1. Create a `Bucket` with a small index capacity so that inserting a modest number of distinct pubkeys forces `IndexNoSpace`.
2. From two threads, concurrently call `BucketApi::try_write` for different pubkeys that hash into the same bucket, both receiving `Err(BucketMapError::IndexNoSpace(cap))` for the same `cap`.
3. Both threads call `BucketApi::grow(err)`, which acquires only a read lock and invokes `Bucket::grow_index` concurrently.
4. Observe: the second thread's `add_reallocation()` call fails the `assert_eq!(0, ..., "Only 1 reallocation can occur at a time")`, panicking the thread/process, and the first thread's newly-built resized index (`items.index = Some(A)`) is discarded/overwritten before that panic is even visible.

Note: I was not able to run this PoC in this environment (read-only code index); the analysis is based on direct reading of the relevant source in `bucket_map/src/bucket.rs`, `bucket_map/src/bucket_api.rs`, and the call sites in `accounts-db/src/accounts_index/`.

### Citations

**File:** bucket_map/src/bucket_api.rs (L103-158)
```rust
    fn get_write_bucket(&self) -> RwLockWriteGuard<'_, Option<Bucket<T>>> {
        let mut bucket = self.bucket.write().unwrap();
        if let Some(bucket) = bucket.as_mut() {
            bucket.handle_delayed_grows();
        } else {
            self.allocate_bucket(&mut bucket);
        }
        bucket
    }

    pub fn insert(&self, pubkey: &Pubkey, value: (&[T], RefCount)) {
        let mut bucket = self.get_write_bucket();
        bucket.as_mut().unwrap().insert(pubkey, value)
    }

    pub fn grow(&self, err: BucketMapError) {
        // grows are special - they get a read lock and modify 'reallocated'
        // the grown changes are applied the next time there is a write lock taken
        if let Some(bucket) = self.bucket.read().unwrap().as_ref() {
            bucket.grow(err)
        }
    }

    /// caller can specify that the index needs to hold approximately `count` entries soon.
    /// This gives a hint to the resizing algorithm and prevents repeated incremental resizes.
    pub fn set_anticipated_count(&self, count: u64) {
        let mut bucket = self.get_write_bucket();
        bucket.as_mut().unwrap().set_anticipated_count(count);
    }

    /// batch insert of `items`. Assumption is a single slot list element and ref_count == 1.
    /// For any pubkeys that already exist, the index in `items` of the failed insertion and the existing data (previously put in the index) are returned.
    pub fn batch_insert_non_duplicates(&self, items: &[(Pubkey, T)]) -> Vec<(usize, T)> {
        let mut bucket = self.get_write_bucket();
        bucket.as_mut().unwrap().batch_insert_non_duplicates(items)
    }

    pub fn update<F>(&self, key: &Pubkey, updatefn: F)
    where
        F: FnMut(Option<(&[T], RefCount)>) -> Option<(Vec<T>, RefCount)>,
    {
        let mut bucket = self.get_write_bucket();
        bucket.as_mut().unwrap().update(key, updatefn)
    }

    pub fn try_write(
        &self,
        pubkey: &Pubkey,
        value: (&[T], RefCount),
    ) -> Result<(), BucketMapError> {
        let mut bucket = self.get_write_bucket();
        bucket
            .as_mut()
            .unwrap()
            .try_write(pubkey, value.0.iter(), value.0.len(), value.1)
    }
```

**File:** bucket_map/src/bucket.rs (L67-83)
```rust
impl<I: BucketOccupied, D: BucketOccupied> Reallocated<I, D> {
    /// specify that a reallocation has occurred
    pub fn add_reallocation(&self) {
        assert_eq!(
            0,
            self.active_reallocations.fetch_add(1, Ordering::Relaxed),
            "Only 1 reallocation can occur at a time"
        );
    }
    /// Return true IFF a reallocation has occurred.
    /// Calling this takes conceptual ownership of the reallocation encoded in the struct.
    pub fn get_reallocated(&self) -> bool {
        self.active_reallocations
            .compare_exchange(1, 0, Ordering::Acquire, Ordering::Relaxed)
            .is_ok()
    }
}
```

**File:** bucket_map/src/bucket.rs (L685-746)
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
```

**File:** accounts-db/src/accounts_index/accounts_index_storage.rs (L51-98)
```rust
impl BgThreads {
    fn new<T: IndexValue, U: DiskIndexValue + From<T> + Into<T>>(
        storage: &Arc<BucketMapHolder<T, U>>,
        in_mem: &[Arc<InMemAccountsIndex<T, U>>],
        threads: NonZeroUsize,
        exit: Arc<AtomicBool>,
    ) -> Self {
        let is_disk_index_enabled = storage.is_disk_index_enabled();
        let num_threads = if is_disk_index_enabled {
            threads.get()
        } else {
            // no disk index, so only need 1 thread to report stats
            1
        };

        // stop signal used for THIS batch of bg threads
        let local_exit = Arc::new(AtomicBool::default());
        let handles = Some(
            (0..num_threads)
                .map(|idx| {
                    // the first thread we start is special
                    let can_advance_age = idx == 0;
                    let storage_ = Arc::clone(storage);
                    let local_exit = local_exit.clone();
                    let system_exit = exit.clone();
                    let in_mem_ = in_mem.to_vec();

                    // note that using rayon here causes us to exhaust # rayon threads and many tests running in parallel deadlock
                    Builder::new()
                        .name(format!("solIdxFlusher{idx:02}"))
                        .spawn(move || {
                            storage_.background(
                                vec![local_exit, system_exit],
                                in_mem_,
                                can_advance_age,
                            );
                        })
                        .unwrap()
                })
                .collect(),
        );

        BgThreads {
            exit: local_exit,
            handles,
            wait: Arc::clone(&storage.wait_dirty_or_aged),
        }
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L433-452)
```rust
    /// Writes `disk_entry` for `pubkey` to `disk`, retrying after a grow if needed.
    /// Returns the total time spent waiting for disk grows, in microseconds.
    fn write_to_disk(
        disk: &BucketApi<(Slot, U)>,
        pubkey: &Pubkey,
        disk_entry: &[(Slot, U)],
    ) -> u64 {
        let mut grow_us = 0u64;
        loop {
            match disk.try_write(pubkey, (disk_entry, 1)) {
                Ok(_) => break,
                Err(err) => {
                    let m = Measure::start("flush_grow");
                    disk.grow(err);
                    grow_us += m.end_as_us();
                }
            }
        }
        grow_us
    }
```

**File:** bucket_map/src/bucket_map.rs (L192-200)
```rust
    /// Get the bucket index for Pubkey `key`
    pub fn bucket_ix(&self, key: &Pubkey) -> usize {
        if self.max_buckets_pow2 > 0 {
            let location = read_be_u64(key.as_ref());
            (location >> (u64::BITS - self.max_buckets_pow2 as u32)) as usize
        } else {
            0
        }
    }
```
