### Title
Restarted disk `Bucket` reuses the index file but discards data buckets, causing panics/wrong reads for `MultipleSlots` entries - (File: `bucket_map/src/bucket.rs`, `bucket_map/src/index_entry.rs`, `bucket_map/src/restart.rs`)

### Summary
The on-disk `BucketMap` used by `InMemAccountsIndex`'s disk index tier persists a "restart file" so that on validator restart the index mmap file can be reused instead of rebuilt from scratch. `Bucket::new` reuses the persisted **index** file via `BucketStorage::load_on_restart`, but always initializes the **data** buckets as an empty `vec![]`, regardless of whether the index was reused. Any index entry with `OccupiedEnum::MultipleSlots` (a slot list of more than one element) stores a `data_bucket_ix`/offset that refers to the old data bucket files, which are never restored. When such an entry is later read, `IndexEntryPlaceInBucket::read_value` indexes into the empty `data_buckets` slice, causing an out-of-bounds panic (or, after new data buckets are created via later inserts/growth, silently reading zero-initialized/incorrect data instead of the persisted slot list).

### Finding Description
`Restart::get_restartable_buckets` (`bucket_map/src/restart.rs`) only tracks and restores the **index** file per bucket (`bucket.file_name`/`random`), matched against files found by `get_all_possible_index_files_in_drives`. Data bucket files are never recorded in the restart header/format and are not looked up on restart. [1](#0-0) 

In `Bucket::new`, when a previous index file can be reused, it is reloaded via `BucketStorage::load_on_restart`, but the `data` field is unconditionally set to an empty vector: [2](#0-1) 

The index file, however, persists entries whose state is `OccupiedEnumTag::MultipleSlots`, which encode a `data_bucket_ix` (derived deterministically from `num_slots`) and a `storage_offset` pointing into what used to be a specific data bucket file: [3](#0-2) 

When such an entry is later read via `IndexEntryPlaceInBucket::read_value`, the code computes `data_bucket_ix` from the persisted `num_slots` and indexes directly into the `data_buckets` slice without any bounds check: [4](#0-3) 

Since `data_buckets` (i.e., `self.data`) is `vec![]` immediately after a restart-reuse of the index file, `data_buckets[data_bucket_ix as usize]` panics with an out-of-bounds index the first time any pubkey with a multi-element slot list (extremely common for real accounts touched across multiple slots) is looked up — well before any new data buckets could have been created by subsequent inserts. This is directly analogous to the reported bug class: state (`ERC721` name/symbol) is initialized once at one code path (constructor/implementation deploy) but not carried over/re-initialized on the other code path (proxy `delegatecall`) that actually gets used at runtime — here, the index file's on-disk state assumes companion data buckets exist, but the restart path never restores or recreates them before they're needed, producing a state mismatch between what the index believes exists and what's actually there.

This restart mechanism is wired up for the accounts index's disk-tier bucket map: `BucketMapHolder::new` sets `restart_config_file` to `accounts_index_restart` under the configured drive, i.e., this is the real, production accounts-index restart path, not test-only code. [5](#0-4) 

### Impact Explanation
This falls under "node panic" and potentially "silent wrong account data" categories permitted by the rules:
- **Panic**: On validator restart with the disk-index restart file present, the first read of an account whose index entry was persisted in the `MultipleSlots` state will panic due to out-of-bounds slice indexing (`data_buckets[data_bucket_ix as usize]` against an empty `Vec`). This can crash a validator on restart entirely, since accounts index lookups happen continuously during replay/banking.
- **Silent wrong data (secondary)**: Even in the edge case where later inserts grow `self.data` with enough empty buckets to avoid a panic (via `apply_grow_data`, which fills intervening empty buckets), the newly created data buckets are zero-initialized and contain none of the previously persisted slot-list bytes, so a lookup would read a `ref_count`/slot list that is wrong or an `is_free` assertion failure (`assert!(!data_bucket.is_free(loc))` in `read_value`) rather than the true persisted account slot history, corrupting the accounts index/lattice consistency between the on-disk index bucket and the in-memory index maintained elsewhere.

### Likelihood Explanation
The disk-tier accounts index (`IndexLimit::Threshold`/`Minimal`) is a supported, documented configuration for validators running with constrained memory, and it explicitly configures a restart file (`accounts_index_restart`) to speed up restarts by reusing bucket files. Any validator running with this configuration that restarts (a routine, frequent operational event, e.g., upgrades or crash recovery) and has accounts with slot lists larger than 1 element (normal for actively-modified accounts) will trigger this path deterministically the next time such an account is looked up post-restart.

### Recommendation
Either:
1. Persist and restore data bucket files the same way index files are restored (extend the `Restart` file format to track data bucket file names per index bucket, analogous to `RestartableBucket`), or
2. If data buckets cannot be safely restored/reused, invalidate/rewrite reused index entries that are in the `MultipleSlots` state (convert them back to `Free`/rebuild) when an index file is reused without its corresponding data buckets, so stale `MultipleSlots` references can never be dereferenced against non-existent data.

### Proof of Concept
1. Configure a validator (or a bucket_map unit test) with `AccountsIndexConfig` using `IndexLimit::Threshold`/`Minimal` and a `drives` path so that `BucketMapConfig.restart_config_file` is set (`accounts_index_restart`), per `BucketMapHolder::new`.
2. Insert enough slot-list entries for a pubkey so its index entry becomes `OccupiedEnumTag::MultipleSlots` (i.e., slot list length > 1), forcing a data bucket allocation and `apply_grow_data`/`add_data_bucket`.
3. Cleanly shut down the process (so `Bucket`/`BucketStorage` drop with `delete_file_on_drop = false` for the reused index file, per `restart.rs`/`bucket_storage.rs::load_on_restart`).
4. Restart the process pointing at the same drives/restart file. `BucketMap::new` → `Restart::get_restartable_buckets` finds the old index file and matches it to a `RestartableBucket`; `Bucket::new` reuses it via `BucketStorage::load_on_restart`, while `data: vec![]`.
5. Call `read_value`/`items()` for the pubkey inserted in step 2. `IndexEntryPlaceInBucket::read_value` computes a nonzero `data_bucket_ix` from the persisted `num_slots` and indexes `data_buckets[data_bucket_ix as usize]` against the empty `Vec`, panicking with an out-of-bounds index.

### Citations

**File:** bucket_map/src/restart.rs (L205-226)
```rust
    /// get one `RestartableBucket` for each bucket.
    /// If a potentially reusable file exists, then put that file's path in `RestartableBucket` for that bucket.
    /// Delete all files that cannot possibly be reused.
    pub(crate) fn get_restartable_buckets(
        restart: Option<&Arc<Mutex<Restart>>>,
        drives: &Arc<Vec<PathBuf>>,
        num_buckets: usize,
    ) -> Vec<RestartableBucket> {
        let mut paths = Self::get_all_possible_index_files_in_drives(drives);
        let results = (0..num_buckets)
            .map(|index| {
                let path = restart.and_then(|restart| {
                    let restart = restart.lock().unwrap();
                    let id = restart.get_bucket(index).file_name;
                    paths.remove(&id)
                });
                RestartableBucket {
                    restart: restart.cloned(),
                    index,
                    path,
                }
            })
```

**File:** bucket_map/src/bucket.rs (L120-178)
```rust
impl<'b, T: Clone + Copy + PartialEq + std::fmt::Debug + 'static> Bucket<T> {
    pub(crate) fn new(
        drives: Arc<Vec<PathBuf>>,
        max_search: MaxSearch,
        stats: Arc<BucketMapStats>,
        count: Arc<AtomicU64>,
        mut restartable_bucket: RestartableBucket,
    ) -> Self {
        let reuse_path = std::mem::take(&mut restartable_bucket.path);
        let elem_size = NonZeroU64::new(std::mem::size_of::<IndexEntry<T>>() as u64).unwrap();
        let (index, random, reused_file_at_startup) = reuse_path
            .and_then(|path| {
                // try to reuse the file this bucket was using last time we were running
                restartable_bucket.get().and_then(|(_file_name, random)| {
                    let result = BucketStorage::load_on_restart(
                        path.clone(),
                        elem_size,
                        max_search,
                        Arc::clone(&stats.index),
                        count.clone(),
                    )
                    .map(|index| (index, random, true /* true = reused file */));
                    if result.is_none() {
                        // we couldn't reuse it, so delete it
                        _ = fs::remove_file(path);
                    }
                    result
                })
            })
            .unwrap_or_else(|| {
                // no file to reuse, so create a new file
                let (index, file_name) = BucketStorage::new(
                    Arc::clone(&drives),
                    1,
                    elem_size.into(),
                    max_search,
                    Arc::clone(&stats.index),
                    count,
                );
                let random = rng().random();
                restartable_bucket.set_file(file_name, random);
                (index, random, false /* true = reused file */)
            });

        stats.index.resize_grow(0, index.capacity_bytes());

        Self {
            random,
            drives,
            index,
            data: vec![],
            stats,
            reallocated: Reallocated::default(),
            anticipated_size: 0,
            at_least_one_entry_deleted: false,
            restartable_bucket,
            reused_file_at_startup,
        }
    }
```

**File:** bucket_map/src/index_entry.rs (L212-277)
```rust
/// required fields when an index element references the data file
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
pub(crate) struct MultipleSlots {
    // if the bucket doubled, the index can be recomputed using storage_cap_and_offset.create_bucket_capacity_pow2
    storage_cap_and_offset: PackedStorage,
    /// num elements in the slot list
    num_slots: Slot,
}

impl MultipleSlots {
    pub(crate) fn set_storage_capacity_when_created_pow2(
        &mut self,
        storage_capacity_when_created_pow2: u8,
    ) {
        self.storage_cap_and_offset
            .set_capacity_when_created_pow2(storage_capacity_when_created_pow2)
    }

    pub(crate) fn set_storage_offset(&mut self, storage_offset: u64) {
        self.storage_cap_and_offset
            .set_offset_checked(storage_offset)
            .expect("New storage offset must fit into 7 bytes!")
    }

    fn storage_capacity_when_created_pow2(&self) -> u8 {
        self.storage_cap_and_offset.capacity_when_created_pow2()
    }

    fn storage_offset(&self) -> u64 {
        self.storage_cap_and_offset.offset()
    }

    pub(crate) fn num_slots(&self) -> Slot {
        self.num_slots
    }

    pub(crate) fn set_num_slots(&mut self, num_slots: Slot) {
        self.num_slots = num_slots;
    }

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

    /// This function maps the original data location into an index in the current bucket storage.
    /// This is coupled with how we resize bucket storages.
    pub(crate) fn data_loc(&self, storage: &BucketStorage<DataBucket>) -> u64 {
        self.storage_offset()
            << (storage.contents.capacity_pow2() - self.storage_capacity_when_created_pow2())
    }

```

**File:** bucket_map/src/index_entry.rs (L442-473)
```rust
    pub(crate) fn read_value<'a>(
        &self,
        index_bucket: &'a BucketStorage<IndexBucket<T>>,
        data_buckets: &'a [BucketStorage<DataBucket>],
    ) -> (&'a [T], RefCount) {
        let mut ref_count = 1;
        let slot_list = match self.get_slot_count_enum(index_bucket) {
            OccupiedEnum::ZeroSlots => {
                // num_slots is 0. This means empty slot list and ref_count=1
                &[]
            }
            OccupiedEnum::OneSlotInIndex(single_element) => {
                // only element is stored in the index entry
                std::slice::from_ref(single_element)
            }
            OccupiedEnum::MultipleSlots(multiple_slots) => {
                // slot list and ref_count are in data file
                let data_bucket_ix =
                    MultipleSlots::data_bucket_from_num_slots(multiple_slots.num_slots);
                let data_bucket = &data_buckets[data_bucket_ix as usize];
                let loc = multiple_slots.data_loc(data_bucket);
                assert!(!data_bucket.is_free(loc));

                ref_count = MultipleSlots::ref_count(data_bucket, loc);
                data_bucket.get_slice::<T>(loc, multiple_slots.num_slots, IncludeHeader::NoHeader)
            }
            _ => {
                panic!("trying to read data from a free entry");
            }
        };
        (slot_list, ref_count)
    }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L278-284)
```rust
        let mut bucket_config = BucketMapConfig::new(bins);
        bucket_config.drives = config.drives.as_ref().cloned();
        bucket_config.restart_config_file = bucket_config
            .drives
            .as_ref()
            .and_then(|drives| drives.first())
            .map(|drive| drive.join("accounts_index_restart"));
```
