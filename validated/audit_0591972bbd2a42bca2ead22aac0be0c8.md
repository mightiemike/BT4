### Title
Disk-index restart files are reused across incompatible `IndexEntry<T>` layouts, causing silent index-storage collision - ([File: bucket_map/src/restart.rs], [File: bucket_map/src/bucket_storage.rs])

### Summary
When the accounts index is configured to persist on disk (`--accounts-index-path`), each `Bucket<T>` records which backing file it used in a `Restart` file so the same file can be memory-mapped and reused on the next process start [1](#0-0) . The `Restart::Header` that gates reuse only encodes `version`, `buckets`, and `max_search` [2](#0-1) ; it never records the size/layout of the `IndexEntry<T>` element that was actually written to the file. `BucketStorage::load_on_restart` blindly derives `num_elems` from `file_len / elem_size`, where `elem_size` is simply `size_of::<IndexEntry<T>>()` computed by the *currently running* binary, with no check that this matches the size the file was created with [3](#0-2) . This is the same class of bug as the reported "storage collision": a persisted, size-sensitive struct layout is reused across a boundary (here, a validator restart/upgrade) without any check that the layout is unchanged, so every subsequent field is silently reinterpreted at the wrong offset.

### Finding Description
`Bucket::new` reuses a prior run's index file whenever `restartable_bucket.get()` returns a previously recorded `(file_name, random)` pair, passing the *current* build's `elem_size = size_of::<IndexEntry<T>>()` into `BucketStorage::load_on_restart` [4](#0-3) . `load_on_restart` computes the reused file's element count purely from `file length / elem_size` and then mmaps the file, treating every `elem_size`-sized slice as a valid `IndexEntry<T>` [5](#0-4) . The `Header` written to the restart-config file records only `version`, `buckets`, and `max_search` — never a value tied to `size_of::<IndexEntry<T>>()` or the bit layout of `PackedRefCount`/`PackedStorage`/`MultipleSlots` that make up that struct [6](#0-5) . `IndexEntry<T>` itself is a `#[repr(C)]` union-backed struct whose size is `32 + 8 + 8` bytes for `T = u64` per the crate's own tests, and that size changes automatically with any change to `T`, `RefCount` packing, or the `MultipleSlots`/`PackedStorage` bitfields [7](#0-6) [8](#0-7) . If a validator restarts after an upgrade that changes any of these types/packings without a corresponding `HEADER_VERSION` bump, `get_restart_file`/`get_restartable_buckets` will happily reuse the stale on-disk bucket file [9](#0-8) , [10](#0-9) , and every entry read back via `IndexEntryPlaceInBucket` (`key`, `get_slot_count_enum`, `read_value`) will parse bytes belonging to a different-sized layout — pubkeys, ref-counts, and slot lists all shift, exactly mirroring the `DiamondCutStorage`/`UpgradeStorage` slot-collision reported in the external bug.

### Impact Explanation
Silent reinterpretation of the disk index means an honest node can, on restart, load an `AccountInfo`/slot-list entry keyed to the wrong pubkey or pointing at the wrong `AppendVec` offset/slot, since the bytes were laid out for a differently-sized struct. This is a concrete stale/wrong-version account-index load: subsequent account reads for the affected bucket slots resolve to unrelated stored data (wrong storage offset, wrong ref_count, or wrong slot list), which can propagate into wrong balances/hashes being computed for the node until the index is rebuilt or `verify_index` catches the mismatch. Because the corruption is systemic (every slot in the bucket shifts uniformly, the same way the whole `AppStorage` shifted), a large fraction of a restarted node's disk-index-backed entries can be affected simultaneously, not just isolated collisions.

### Likelihood Explanation
This requires: (1) the operator to use the disk accounts-index feature (`--accounts-index-path`), and (2) the running binary's `IndexEntry<T>`/`RefCount`/`PackedStorage` layout to differ from the one that wrote the persisted bucket files, while `HEADER_VERSION` is left unchanged — i.e., exactly the "upgrade changes a persisted struct's size but the compatibility guard isn't bumped" scenario from the report. There is no code path that fails safe on an elem_size mismatch (unlike the `buckets`/`max_search`/`version` checks that already exist), so any future PR touching these disk-format-relevant types without remembering to bump `HEADER_VERSION` reintroduces this. This is a real, currently-unguarded gap rather than a theoretical one, though it requires a specific type of regression to trigger, which lowers immediate likelihood.

### Recommendation
Encode a layout fingerprint (e.g., `size_of::<IndexEntry<T>>()`, or better, a compile-time/const hash of the packed bit layout) into the `Restart` `Header`/`OneIndexBucket` records, and reject reuse (falling back to a fresh, empty bucket file, as already done for `version`/`buckets`/`max_search` mismatches) whenever the stored fingerprint doesn't match the currently running binary's `IndexEntry<T>` layout. Additionally, add a `debug_assert!`/runtime check in `BucketStorage::load_on_restart` that the reused file's length is an exact multiple of the current `elem_size` and, ideally, includes a persisted `elem_size` value written at bucket-file-creation time and compared before every reuse.

### Proof of Concept
1. Configure a node with `--accounts-index-path` so disk buckets and the restart file are used.
2. Run the node so `Restart::new`/`Bucket::new` create and record bucket files sized for the current `IndexEntry<T>` layout [11](#0-10) .
3. Upgrade to a build where `size_of::<IndexEntry<T>>()` differs (e.g., a change to `PackedStorage`/`MultipleSlots` bit widths or the generic `T`) but `HEADER_VERSION` in `bucket_map/src/restart.rs` is not incremented.
4. Restart the node: `Restart::get_restart_file` accepts the old header (version/buckets/max_search unchanged) [12](#0-11) , and `BucketStorage::load_on_restart` mmaps the old file using the new `elem_size`, producing `IndexEntry<T>` values whose `key`/`contents` fields are read from the wrong byte offsets [5](#0-4) .
5. Observe that `Bucket::keys()`/`read_value()` return pubkeys/slot-list data that do not correspond to what was actually stored, demonstrating the storage collision.

### Citations

**File:** bucket_map/src/restart.rs (L1-16)
```rust
//! Persistent info of disk index files to allow files to be reused on restart.
use {
    crate::bucket_map::{BucketMapConfig, MAX_SEARCH_DEFAULT},
    bytemuck_derive::{Pod, Zeroable},
    memmap2::MmapMut,
    std::{
        collections::HashMap,
        fmt::{Debug, Formatter},
        fs::{self, OpenOptions, remove_file},
        path::{Path, PathBuf},
        sync::{Arc, Mutex},
    },
};

/// written into file. Change this if expected file contents change.
const HEADER_VERSION: u64 = 1;
```

**File:** bucket_map/src/restart.rs (L19-37)
```rust
#[derive(Debug, Pod, Zeroable, Copy, Clone)]
#[repr(C)]
pub(crate) struct Header {
    /// version of this file. Differences here indicate the file is not usable.
    version: u64,
    /// number of buckets these files represent.
    buckets: u64,
    /// u8 representing how many entries to search for during collisions.
    /// If this is different, then the contents of the index file's contents are likely not as helpful.
    max_search: u8,
    /// padding to make size of Header be an even multiple of u128
    _dummy: [u8; 15],
}

// In order to safely guarantee Header is Pod, it cannot have any padding.
const _: () = assert!(
    std::mem::size_of::<Header>() == std::mem::size_of::<u128>() * 2,
    "incorrect size of header struct"
);
```

**File:** bucket_map/src/restart.rs (L147-177)
```rust
    pub(crate) fn get_restart_file(config: &BucketMapConfig) -> Option<Restart> {
        let path = config.restart_config_file.as_ref()?;
        let metadata = std::fs::metadata(path).ok()?;
        let file_len = metadata.len();

        let expected_len = Self::expected_len(config.max_buckets);
        if expected_len as u64 != file_len {
            // mismatched len, so ignore this file
            return None;
        }

        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(false)
            .open(path)
            .ok()?;
        let mmap = unsafe { MmapMut::map_mut(&file).unwrap() };

        let restart = Restart { mmap };
        let header = restart.get_header();
        if header.version != HEADER_VERSION
            || header.buckets != config.max_buckets as u64
            || header.max_search != config.max_search.unwrap_or(MAX_SEARCH_DEFAULT)
        {
            // file doesn't match our current configuration, so we have to restart with fresh buckets
            return None;
        }

        Some(restart)
    }
```

**File:** bucket_map/src/restart.rs (L208-235)
```rust
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
            .collect();

        paths.into_iter().for_each(|path| {
            // delete any left over files that we won't be using
            _ = fs::remove_file(path.1);
        });

        results
    }
```

**File:** bucket_map/src/bucket_storage.rs (L227-253)
```rust
    pub(crate) fn load_on_restart(
        path: PathBuf,
        elem_size: NonZeroU64,
        max_search: MaxSearch,
        stats: Arc<BucketStats>,
        count: Arc<AtomicU64>,
    ) -> Option<Self> {
        let offset = Self::get_offset_to_first_data();
        let num_elems = std::fs::metadata(&path)
            .ok()
            .map(|metadata| metadata.len().saturating_sub(offset) / elem_size)?;
        if num_elems == 0 {
            return None;
        }
        let mmap = Self::map_open_file(&path, false, 0, &stats)?;
        Some(Self {
            path,
            mmap,
            cell_size: elem_size.into(),
            count,
            stats,
            max_search,
            contents: O::new(Capacity::Actual(num_elems)),
            // since we loaded it, it persisted from last time, so we obviously want to keep it present disk.
            delete_file_on_drop: false,
        })
    }
```

**File:** bucket_map/src/bucket.rs (L120-148)
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
```

**File:** bucket_map/src/bucket.rs (L149-162)
```rust
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
```

**File:** bucket_map/src/index_entry.rs (L188-220)
```rust
#[repr(C)]
#[derive(Copy, Clone)]
/// one instance of this per item in the index
/// stored in the index bucket
pub struct IndexEntry<T: Clone + Copy> {
    pub(crate) key: Pubkey, // can this be smaller if we have reduced the keys into buckets already?
    /// depends on the contents of ref_count.slot_count_enum
    contents: SingleElementOrMultipleSlots<T>,
}

/// 63 bits available for ref count
pub(crate) const MAX_LEGAL_REFCOUNT: RefCount = RefCount::MAX >> 1;

/// hold a big `RefCount` while leaving room for extra bits to be used for things like 'Occupied'
#[bitfield(bits = 64)]
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
pub(crate) struct PackedRefCount {
    /// whether this entry in the data file is occupied or not
    pub(crate) occupied: B1,
    /// ref_count of this entry. We don't need any where near 63 bits for this value
    pub(crate) ref_count: B63,
}

/// required fields when an index element references the data file
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
pub(crate) struct MultipleSlots {
    // if the bucket doubled, the index can be recomputed using storage_cap_and_offset.create_bucket_capacity_pow2
    storage_cap_and_offset: PackedStorage,
    /// num elements in the slot list
    num_slots: Slot,
}
```

**File:** bucket_map/src/index_entry.rs (L526-530)
```rust
    #[test]
    fn test_size() {
        assert_eq!(std::mem::size_of::<PackedStorage>(), 1 + 7);
        assert_eq!(std::mem::size_of::<IndexEntry<u64>>(), 32 + 8 + 8);
    }
```
