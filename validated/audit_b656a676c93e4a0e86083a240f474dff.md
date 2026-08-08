### Title
Disk index/data bucket files are reused across validator restarts without any format/version check, allowing stale or incompatible bytes to be silently reinterpreted as valid `AccountsIndex` entries - ([File: bucket_map/src/bucket_storage.rs])

### Summary
The disk-backed accounts index (`bucket_map`) persists index/data files across restarts to speed up startup. When reopening a bucket file at restart, `BucketStorage::load_on_restart` derives the number of usable elements purely from the file's byte length divided by the caller-supplied `elem_size`, and then blindly re-interprets the mmap'd bytes as `IndexEntry<T>`/`DataBucket` records. There is no embedded content/version tag in the bucket data file itself that is checked against the current binary's expected layout, unlike the sibling `Restart` config file, which does carry an explicit `HEADER_VERSION` and rejects mismatches.

### Finding Description
`BucketStorage::load_on_restart` computes `num_elems` from `metadata.len()` and the runtime-computed `elem_size` (`size_of::<IndexEntry<T>>()`), then maps the file and treats its bytes as an array of `IndexEntry<T>`/`DataBucket` elements without any check that the file was actually written with the same struct layout/format version: [1](#0-0) 

The reuse decision at `Bucket::new` calls `load_on_restart` directly with the elem size computed from the current binary's type layout, and unconditionally trusts a successful load (any file whose length happens to be divisible by `elem_size`) as valid, previously-written index data: [2](#0-1) 

Contrast this with the separate `Restart` config file format, which *does* embed an explicit version discriminant and refuses to reuse files whose version/bucket-count/max-search don't match: [3](#0-2) [4](#0-3) 

That version check only gates the small `Header`/`OneIndexBucket` restart-mapping file (which just maps bucket index -> file name/random seed) — it does **not** propagate any format guarantee to the actual per-bucket index/data files that `load_on_restart` reopens. `get_restartable_buckets` only matches files by an opaque `u128` file-name id recorded in the (checked) restart file, and once a candidate path is found, `Bucket::new` hands it to `load_on_restart` with zero structural validation of the file's actual contents: [5](#0-4) 

This is the same bug class as the Curve pool report: code assumes a specific data "shape"/version for an external resource (there, a Curve pool ABI; here, a previously-written bucket data file) without verifying compatibility, and silently proceeds to consume the bytes under the wrong interpretation instead of failing safe. If `IndexEntry<T>`'s in-memory layout or semantics change between two agave releases running on the same machine (e.g., a struct field is added/reordered, encoding of `OccupiedEnum` changes, or `T`'s size changes) while file length still divides evenly by the new `elem_size` (a very plausible coincidence for fixed-size PODs), a restarted validator will silently load garbage/incompatible bytes as `IndexEntry`/`DataBucket` records into the live in-memory accounts index rather than failing to load or falling back to a clean rebuild.

### Impact Explanation
Because these on-disk bucket files back the `AccountsIndex`'s ref-counts and slot-list offsets/pointers into `AppendVec` storages, silently misinterpreting stale/incompatible bytes can produce wrong `AccountInfo` entries (bogus storage offsets, wrong ref counts, or bogus keys) that are then used to serve account reads. This falls squarely into "concrete stale or wrong-version account loads" and can manifest as a validator panic when an offset points outside of a valid append-vec (attempting to read a stored account past bounds), or silent data corruption in the index causing incorrect account content to be returned for a pubkey until the index is separately reconciled/rebuilt. Unlike the vault's stuck 0-address, an agave validator hitting this would not fail cleanly — it would keep running with a possibly-corrupted index, an assurance-breaking outcome for a validator/consensus node.

### Likelihood Explanation
This path executes automatically and unconditionally for any operator who restarts a validator that reuses its accounts-index drives (the common "fastboot"-style restart flow) whenever `BucketMapConfig::restart_config_file` is configured. It requires no attacker/malicious input — only a binary upgrade (or any change altering `IndexEntry<T>`'s layout/size in a way that coincidentally still divides file length evenly) between two runs on the same node, or file corruption. The Curve-report parallel here (version mismatch handled implicitly, not explicitly checked) is the same failure mode, and the codebase's own inconsistency — checking version for the restart-mapping file but not the underlying data files it points to — makes this a real gap rather than a purely theoretical one.

### Recommendation
Embed an explicit format/version tag (and ideally the exact `size_of::<IndexEntry<T>>()`/layout discriminant) in each on-disk bucket index/data file itself, and have `load_on_restart` validate it before trusting the file's contents, mirroring the checks already done in `Restart::get_restart_file` (`header.version != HEADER_VERSION` rejection). On any mismatch, treat the file as unusable (delete and fall back to creating a fresh bucket) rather than reinterpreting its bytes.

### Proof of Concept
1. Run a validator with `BucketMapConfig::restart_config_file` set, so index/data bucket files persist under `drives` across restarts.
2. Upgrade to a build where `IndexEntry<T>` (or `DataBucket`) layout changes in a way where the new `size_of::<IndexEntry<T>>()` still evenly divides the old file's byte length (e.g., same size but reinterpreted field semantics/`OccupiedEnum` discriminant values, or a size change that is still an exact divisor of the on-disk length).
3. Restart the validator: `Restart::get_restart_file` succeeds because `HEADER_VERSION`/`buckets`/`max_search` in the small mapping file are unchanged; `get_restartable_buckets` resolves the same file id/path; `Bucket::new` -> `BucketStorage::load_on_restart` succeeds because the length is divisible by the current `elem_size`.
4. The old bytes are now interpreted through the new layout as live `IndexEntry`/`DataBucket` records with no verification, producing wrong keys/offsets/ref-counts in the in-memory accounts index that are exercised on subsequent account reads.

### Citations

**File:** bucket_map/src/bucket_storage.rs (L226-253)
```rust
    /// load and mmap the file that is this disk bucket if possible
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

**File:** bucket_map/src/restart.rs (L205-235)
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
            .collect();

        paths.into_iter().for_each(|path| {
            // delete any left over files that we won't be using
            _ = fs::remove_file(path.1);
        });

        results
    }
```
