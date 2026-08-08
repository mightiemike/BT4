### Title
Bucket-map disk-index "restart" reuse validates version/bucket-count/max_search but not per-entry layout, allowing silent misinterpretation of stale on-disk index data - (File: bucket_map/src/restart.rs)

### Summary
The bug class from the external report is "code assumes an external/underlying interface is compatible with what it was written for, without validating the actual layout, and blindly reinterprets the data" (Uniswap V3 `slot0`/`observations`/`observe` layout assumed for a structurally different Camelot pool). The closest reachable analog in `agave` is in the AccountsDB disk `bucket_map` index restart/reuse mechanism: on validator restart, a previously-written index-bucket file is reused and reinterpreted as an array of `IndexEntry<T>` records using only three coarse compatibility checks (`version`, `buckets`, `max_search`) — never validating that the actual per-element byte layout/size (`size_of::<IndexEntry<T>>()`) matches what produced the file.

### Finding Description
`Restart::new`/`Restart::get_restart_file` persist and validate a small `Header` containing only `version`, `buckets`, and `max_search`: [1](#0-0) 

`get_restart_file` accepts a previously-written restart/index file as reusable purely based on these three fields matching the current `BucketMapConfig`: [2](#0-1) 

Crucially, the persisted header contains no record of the per-entry element size (`size_of::<IndexEntry<T>>()`), which is what actually determines how the raw bytes of the reused disk-index file must be sliced and reinterpreted. That size is instead recomputed purely at runtime from the current build's generic type `T`: [3](#0-2) 

The recomputed `elem_size` is then handed to `BucketStorage::load_on_restart`, which trusts it completely: it divides the on-disk file length by this caller-supplied `elem_size` to compute `num_elems`, and then mmaps the raw file and treats it as an array of `IndexEntry<T>` at that stride, with no check that the file was actually written with that stride: [4](#0-3) 

If the on-disk layout of `IndexEntry<T>` (or the underlying `T`, i.e. the accounts-index value/slot-list element type) ever changes between validator builds/versions while `version`, `buckets`, and `max_search` stay the same — exactly the kind of silent structural drift the Camelot report warns about (assuming interface/layout compatibility instead of checking it) — a stale index file surviving from a prior run would be reused and its bytes reinterpreted under the new stride. This is functionally identical to the reported bug class: the code assumes the underlying persisted structure is compatible with what the current code expects to read (`slot0`/`observations` vs `globalState`/`timepoints`), and blindly calls into it instead of validating structural compatibility.

### Impact Explanation
Misinterpreting the raw bytes of the disk index bucket as `IndexEntry<T>` values with the wrong stride corrupts every entry: pubkeys, occupied/free flags, ref-counts, and the embedded `AccountInfo`/slot-list data (storage id + offset used to locate the actual account record in AccountsDB storage) would all be read from wrong byte offsets. This falls squarely into the accepted impact category of "concrete stale or wrong-version account loads": the accounts index could point a pubkey lookup at the wrong storage offset, causing the validator to load stale, wrong, or garbage account data (or panic on an out-of-bounds/invalid read) after a restart with a stale-but-header-matching index file, which can lead to hash/capitalization divergence versus honest peers that started clean.

### Likelihood Explanation
Likelihood is low-to-moderate and depends on whether a real-world code change to the disk-index value type (`T`, e.g. an `AccountInfo`-like struct) can occur without a corresponding bump/consideration in `HEADER_VERSION`, `buckets`, or `max_search`. Since `HEADER_VERSION` is a single hardcoded constant that engineers must remember to bump whenever the on-disk entry layout changes, and nothing in `load_on_restart`/`Bucket::new` independently double-checks the element size against what is recorded in the file, this is a maintenance-hazard class of bug rather than one requiring attacker action — it can be triggered by an ordinary validator software upgrade/rollback sequence that changes `IndexEntry<T>`'s layout without bumping `HEADER_VERSION`.

### Recommendation
Persist the actual per-entry size (`size_of::<IndexEntry<T>>()`) — not just an opaque `version` constant — in the `Restart` `Header`, and validate it in `Restart::get_restart_file` and/or `BucketStorage::load_on_restart` before trusting the file's length/stride. Reject and discard (rather than silently reuse) any file whose recorded element size does not exactly match the current build's `size_of::<IndexEntry<T>>()`, mirroring how `max_search`/`buckets` mismatches are already handled defensively today.

### Proof of Concept
Not applicable as a runnable exploit (this is a structural/maintenance defect, not something an external attacker triggers directly): reachability requires a code change to `IndexEntry<T>`'s layout across validator versions without incrementing `HEADER_VERSION`. Conceptually:
1. Validator version A runs with `IndexEntry<T>` of size `S1`, writes disk-index bucket files and a `Restart` header with `version = HEADER_VERSION`, `buckets = N`, `max_search = M`.
2. Validator is upgraded to version B, in which `IndexEntry<T>` layout changes to size `S2 != S1`, but `HEADER_VERSION`/`buckets`/`max_search` are left unchanged.
3. On restart, `Restart::get_restart_file` ( [5](#0-4) ) accepts the old file because version/buckets/max_search still match.
4. `Bucket::new` ( [6](#0-5) ) calls `BucketStorage::load_on_restart` with `elem_size = S2`, and the function computes `num_elems = file_len / S2` ( [7](#0-6) ) against a file actually laid out with stride `S1`, silently misreading every index entry.

### Citations

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

**File:** bucket_map/src/bucket.rs (L121-141)
```rust
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
```

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
