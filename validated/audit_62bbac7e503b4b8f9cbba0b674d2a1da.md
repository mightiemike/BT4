### Title
Disk-index restart file lacks a layout/type version bound to `IndexEntry<T>`/bucket struct layout, allowing silent misinterpretation of index bytes across binary upgrades - (File: bucket_map/src/restart.rs, bucket_map/src/bucket_storage.rs)

### Summary
The Sherlock report's root cause is that upgradeable contracts reuse raw storage slots across versions without a mechanism (storage gaps / initializer guards) to prevent a new implementation from misinterpreting bytes laid out by an old implementation. The analogous pattern exists in the accounts index's on-disk bucket-map "restart" mechanism: `Restart::get_restart_file` only validates a hand-maintained `HEADER_VERSION` constant, the bucket count, and `max_search` before trusting a previously-written index file and reinterpreting its raw bytes as the current binary's `IndexEntry<T>`/`OneIndexBucket`/`PackedRefCount`/`MultipleSlots`/`PackedStorage` layouts.

### Finding Description
`Restart::get_restart_file` gates reuse of the restart mmap on three fields only: `header.version == HEADER_VERSION`, `header.buckets == config.max_buckets`, and `header.max_search == config.max_search` [1](#0-0) . `HEADER_VERSION` is a plain hardcoded constant that a developer must remember to bump manually [2](#0-1) ; nothing ties it to the actual on-disk record layouts (`IndexEntry<T>`, `PackedRefCount`, `PackedStorage`, `MultipleSlots`) the way `frozen_abi`/ABI-digest checks are used elsewhere in the snapshot code to hard-fail on wire-format drift (e.g. `abi_digest` checks in `runtime/src/serde_snapshot.rs`) [3](#0-2) .

Once the restart header passes these three checks, `Restart::get_restartable_buckets` hands each bucket its previously used `(file_name, random)`, and `BucketStorage::load_on_restart` reopens the file and reconstructs a `BucketStorage<O>` purely from the file length divided by the *current* binary's `elem_size` (`std::mem::size_of::<IndexEntry<T>>()`), with no check that the stored bytes were actually produced with that same struct layout:

```
pub(crate) fn load_on_restart(
    path: PathBuf,
    elem_size: NonZeroU64,
    ...
) -> Option<Self> {
    let offset = Self::get_offset_to_first_data();
    let num_elems = std::fs::metadata(&path)
        .ok()
        .map(|metadata| metadata.len().saturating_sub(offset) / elem_size)?;
    ...
    let mmap = Self::map_open_file(&path, false, 0, &stats)?;
    Some(Self { ..., cell_size: elem_size.into(), contents: O::new(Capacity::Actual(num_elems)), ... })
}
``` [4](#0-3) 

The occupied/free bit and ref-count packing that determines whether a raw cell is trusted as a live index entry live in bit-packed structs with no self-describing tag, e.g. `PackedRefCount` (`occupied: B1`, `ref_count: B63`) and `MultipleSlots`/`PackedStorage` [5](#0-4) . If a future agave release changes any of these struct layouts (adds/reorders a field, changes bit-widths, or changes `IndexEntry<T>`'s generic parameter/size for a new account index value type) but does not also bump `HEADER_VERSION`, an operator upgrading the validator binary while keeping the same `--accounts-index-path`/restart config would have the new binary blindly reopen the old-format bucket files, reinterpret raw mmap bytes under the new layout, and treat garbage bit patterns as valid `occupied`/`ref_count`/`storage_offset` fields. Unlike the restart-file's own `Header`/`OneIndexBucket`, which are protected only against gross size/version drift and not against subtle layout renumbering, there is no compile-time or run-time proof that the bucket record layout used to write these files matches the layout currently compiling `bucket_map`.

### Impact Explanation
Silent corruption of the accounts index on restart can (a) cause an occupied slot to be misread as free — the fast-path index (used by `accounts_index`/`bucket_map_holder.rs`) then loses track of a pubkey's slot list, causing subsequent reads to miss existing accounts or fall back to stale/garbage data offsets, or (b) cause an old free/garbage cell to be misread as occupied with a bogus `data_bucket_ix`/`storage_offset`, causing the account index to resolve a pubkey to the wrong `AccountStorageEntry`/offset and return incorrect account data (stale balance or wrong owner) — a hash/capitalization divergence between the honest node's on-disk state and what other validators compute. Because this happens entirely within an honest node's own restart path (no attacker input required), it maps to the report's "storage corruption during upgrade" impact class.

### Likelihood Explanation
Likelihood is tied specifically to internal agave releases changing the disk-index binary layout (`IndexEntry<T>`, `PackedRefCount`, `PackedStorage`, `MultipleSlots`) without bumping `HEADER_VERSION`, combined with an operator persisting the accounts-index directory (restart file) across a binary upgrade instead of clearing it. This is plausible in normal upgrade operations since `--accounts-index-path` config is durable to encourage fast restarts, and nothing in the code enforces that a layout change is accompanied by a version bump — it is a manual, unverified convention rather than a structural guarantee (contrast with the frozen-abi digest mechanism protecting the snapshot wire format).

### Recommendation
- Derive `HEADER_VERSION` (or an additional field) from a compile-time hash/digest of the actual on-disk record layouts (`IndexEntry<T>`, `PackedRefCount`, `PackedStorage`, `MultipleSlots`, `OneIndexBucket`), similar to the `frozen_abi`/`abi_digest` mechanism already used for snapshots, so any layout change automatically invalidates old restart files instead of relying on a developer to remember to bump a constant.
- Store `elem_size` (and ideally a type tag for `T`) in the restart `Header`/`OneIndexBucket` and validate it in `load_on_restart`/`get_restart_file`, rather than only trusting `metadata.len() / elem_size` computed from the current binary's struct size.
- Consider adding explicit reserved/gap bytes in `Header`/`OneIndexBucket`/index-entry structs to allow additive evolution without reinterpreting existing fields, mirroring the "add storage gaps" mitigation from the report.

### Proof of Concept
Not directly exploitable by an external attacker; this is a code-maintenance/upgrade-safety gap. It can be demonstrated by: (1) writing a restart file and bucket files with the current `IndexEntry<T>`/`PackedRefCount` layout, (2) modifying the bit-widths/field order of `PackedRefCount`/`MultipleSlots`/`PackedStorage` in a local build without bumping `HEADER_VERSION`, (3) restarting `BucketMap` against the old files, and observing that `Restart::get_restart_file` still accepts the file (version/buckets/max_search unchanged) and `load_on_restart` reconstructs entries with corrupted `occupied`/`ref_count`/`storage_offset` values from the reinterpreted bytes, as seen in the restart round-trip test `test_load_on_restart` [6](#0-5) .

### Citations

**File:** bucket_map/src/restart.rs (L15-16)
```rust
/// written into file. Change this if expected file contents change.
const HEADER_VERSION: u64 = 1;
```

**File:** bucket_map/src/restart.rs (L166-176)
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
```

**File:** runtime/src/serde_snapshot.rs (L548-556)
```rust
#[cfg_attr(
    feature = "frozen-abi",
    derive(Deserialize, Serialize, SchemaWrite, StableAbi, StableAbiSample),
    frozen_abi(
        abi_digest = "2TVKjhahaEGqUZAJtMmaaagcxWzhMPUsNrVHsSoNboK7",
        abi_serializer = ["bincode", "wincode"],
        test_roundtrip = "wire_only"
    )
)]
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

**File:** bucket_map/src/bucket_storage.rs (L655-722)
```rust
    #[test]
    fn test_load_on_restart() {
        for request in [Some(7), None] {
            let tmpdir = tempdir().unwrap();
            let paths: Vec<PathBuf> = vec![tmpdir.path().to_path_buf()];
            assert!(!paths.is_empty());
            let drives = Arc::new(paths);
            let num_elems = 1;
            let elem_size = std::mem::size_of::<crate::index_entry::IndexEntry<u64>>() as u64;
            let max_search = 1;
            let stats = Arc::new(BucketStats::default());
            let count = Arc::new(AtomicU64::default());
            let mut storage = if let Some(actual_elems) = request {
                BucketStorage::<IndexBucket<u64>>::new_with_capacity(
                    drives,
                    num_elems,
                    elem_size,
                    Capacity::Actual(actual_elems),
                    max_search,
                    stats.clone(),
                    count.clone(),
                )
                .0
            } else {
                BucketStorage::<IndexBucket<u64>>::new(
                    drives,
                    num_elems,
                    elem_size,
                    max_search,
                    stats.clone(),
                    count.clone(),
                )
                .0
            };
            let expected_capacity = storage.capacity();
            (0..num_elems).for_each(|ix| {
                assert!(storage.is_free(ix));
                assert!(storage.occupy(ix, false).is_ok());
            });
            storage.delete_file_on_drop = false;
            let len = storage.mmap.len();
            (0..expected_capacity as usize).for_each(|i| {
                storage.mmap[i] = (i % 256) as u8;
            });
            // close storage
            let path = storage.path.clone();
            drop(storage);

            // re load and remap storage file
            let storage = BucketStorage::<IndexBucket<u64>>::load_on_restart(
                path,
                NonZeroU64::new(elem_size).unwrap(),
                max_search,
                stats,
                count,
            )
            .unwrap();
            assert_eq!(storage.capacity(), expected_capacity);
            assert_eq!(len, storage.mmap.len());
            (0..expected_capacity as usize).for_each(|i| {
                assert_eq!(storage.mmap[i], (i % 256) as u8);
            });
            (0..num_elems).for_each(|ix| {
                // all should be marked as free
                assert!(storage.is_free(ix));
            });
        }
    }
```

**File:** bucket_map/src/index_entry.rs (L202-220)
```rust
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
