## Analysis

The EIP‑1967 bug class is about a persisted storage region being reinterpreted by "logic" code whose implicit layout assumptions have silently drifted (inheritance order changes / new fields) — with no explicit versioned/fixed layout to catch the mismatch. The closest analog in this codebase is the **accounts‑index disk bucket‑map restart mechanism**, which persists index entries in mmap'd files across validator restarts and reconstructs the byte layout purely from the *current* build's `size_of::<IndexEntry<T>>()`, without persisting or verifying that value alongside the data.

### Title
Disk index restart files can be silently reinterpreted with a mismatched `IndexEntry<T>` cell size across validator versions - (`bucket_map/src/restart.rs`, `bucket_map/src/bucket.rs`)

### Summary
`BucketMap<(Slot, U)>` (used by `AccountsIndex` for its on-disk index) persists its index entries in raw mmap'd files and, on restart, tries to reuse those files via `Restart::get_restart_file` / `Restart::get_restartable_buckets`. The compatibility check only compares `version`, `buckets`, and `max_search` in the `Header` struct; it never records or validates the per-entry cell size (`size_of::<IndexEntry<T>>()`), which is derived from the generic disk-index value type and computed fresh at every startup in `Bucket::new`.

### Finding Description
`Bucket::new` computes `elem_size` from the *current* binary's type layout and feeds it directly into `BucketStorage::load_on_restart`, which blindly divides the file length by that `elem_size` to determine `num_elems` and uses it as `cell_size` for all subsequent offset math (`get_start_offset_with_header`, `get_header`, `get`, etc.): [1](#0-0) [2](#0-1) 

The persisted `restart` control file only records `version`, `buckets`, and `max_search` — nothing that captures the layout/size of `IndexEntry<T>` (i.e., nothing tied to the actual `DiskIndexValue` type, e.g. `AccountInfo`, whose on-disk representation is a packed bitfield): [3](#0-2) [4](#0-3) 

`HEADER_VERSION` is a manually-maintained constant that a developer must remember to bump whenever "expected file contents change." If a future change to `AccountInfo`/`PackedOffsetAndFlags` (`accounts-db/src/account_info.rs`) or to `IndexEntry`'s `PackedRefCount`/`MultipleSlots` (`bucket_map/src/index_entry.rs`) alters `size_of::<IndexEntry<T>>()` without a corresponding `HEADER_VERSION` bump, an upgraded validator restarting on an old data directory will reuse the previous run's raw bucket files under the new, differently-sized cell layout. Because `cell_size` and `offset_to_first_data` are derived purely from the new build's struct sizes, every entry after the first is read from the wrong byte offset, causing the packed `ref_count`/`occupied` bit, `storage_cap_and_offset`, and `num_slots` fields (`bucket_map/src/index_entry.rs`) to be reinterpreted from garbage bytes — the exact "storage collision from lack of a fixed/versioned layout" pattern described in the report, just applied to a persisted index file rather than an EVM proxy slot.

### Impact Explanation
Corrupted `IndexEntry` interpretation directly changes what `data_bucket_ix`, `storage_offset`, and `ref_count` resolve to for a pubkey, which the `AccountsIndex` uses to locate an account's `AppendVec` slot data. This can produce concrete stale/wrong-version account loads (wrong `AccountInfo` retrieved for a pubkey), or a hard node panic when a garbage offset/index is used to index into `BucketStorage`'s mmap (`get_slice`/`get_slice_mut` debug_asserts on slice bounds). This falls squarely in the accepted "stale/wrong-version account loads" and "node panic" impact categories.

### Likelihood Explanation
This is not attacker-triggerable by an unprivileged network peer; it is a latent correctness hazard tied to routine validator upgrades that reuse an existing ledger/accounts-index directory across versions. It requires only a normal restart with `IndexLimit::Minimal`/`Threshold` (disk index enabled) and a future struct-layout change to the disk index value type that isn't paired with a `HEADER_VERSION` bump — plausible given the header-version bump is a manual/human-enforced convention rather than something derived automatically from `size_of::<IndexEntry<T>>()`.

### Recommendation
Persist `size_of::<IndexEntry<T>>()` (or a hash/type-id of the disk index value layout) in the `Header`/`OneIndexBucket` restart metadata and validate it in `Restart::get_restart_file`, in addition to `version`, `buckets`, and `max_search`. This makes the layout compatibility check self-verifying instead of depending on developers remembering to manually bump `HEADER_VERSION` whenever `AccountInfo`/`IndexEntry` layout changes — analogous to fixing storage slots at derived/explicit positions per EIP-1967 rather than relying on implicit, order-dependent layout.

### Proof of Concept
1. Run a validator build where `size_of::<IndexEntry<AccountInfo>>()` == N, with disk index enabled (`--accounts-index-limit` not `unlimited`), and let it persist bucket files + `accounts_index_restart` control file.
2. Upgrade to a build where `AccountInfo`/`PackedOffsetAndFlags` (or another field in `IndexEntry`) changes such that `size_of::<IndexEntry<AccountInfo>>()` != N, but `HEADER_VERSION` in `bucket_map/src/restart.rs` is left unchanged (as demonstrated by test `test_restartable_bucket_load`, only an explicit `version` bump is what invalidates the file: [5](#0-4) ).
3. Restart the validator against the same accounts-index directory. `Restart::get_restart_file` passes (version/buckets/max_search unchanged), and `Bucket::new`/`BucketStorage::load_on_restart` reinterpret the old N-byte-per-entry file using the new cell size, corrupting `ref_count`/`storage_offset` reads for all entries beyond index 0.

### Citations

**File:** bucket_map/src/bucket.rs (L128-141)
```rust
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

**File:** bucket_map/src/restart.rs (L15-31)
```rust
/// written into file. Change this if expected file contents change.
const HEADER_VERSION: u64 = 1;

/// written into file at top.
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
```

**File:** bucket_map/src/restart.rs (L166-177)
```rust
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

**File:** bucket_map/src/restart.rs (L490-497)
```rust
            // create file with different header
            let restart = Arc::new(Mutex::new(Restart::new(&config).unwrap()));
            test_default_restart(&restart, &config);
            restart.lock().unwrap().get_header_mut().version = HEADER_VERSION + 1;
            drop(restart);
            // unsuccessful: header wrong
            let restart = Restart::get_restart_file(&config);
            assert!(restart.is_none());
```
