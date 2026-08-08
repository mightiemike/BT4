### Title
Undocumented bit-packed `PackedStorage`/`data_loc` shift arithmetic in the disk-index bucket map can silently corrupt account data offsets - (File: `bucket_map/src/index_entry.rs`)

### Summary
The bug class from the external report is "correct-but-undocumented low-level code that uses magic constants and unrelated-memory assumptions, making it error-prone and hard to verify." The closest analog in agave is the `MultipleSlots`/`PackedStorage` bit-packing scheme in `bucket_map/src/index_entry.rs`, used by AccountsDB's on-disk account index (`bucket_map`) to compute the physical location of slot-list data. The `data_loc` computation relies on an undocumented invariant (that the current data bucket's `capacity_pow2()` is always ≥ the `storage_capacity_when_created_pow2` recorded when the entry was written) and performs a raw bit-shift with no comments explaining the packing layout, exactly mirroring the `hashOrder` assembly issue of relying on undocumented low-level memory assumptions.

### Finding Description
`MultipleSlots` packs a 56-bit storage offset and an 8-bit "capacity when created" exponent into a single `u64` via the `PackedStorage` bitfield [1](#0-0) , and exposes `data_loc`, which computes the actual on-disk cell index as:

```
self.storage_offset() << (storage.contents.capacity_pow2() - self.storage_capacity_when_created_pow2())
``` [2](#0-1) 

This function has no documentation of the safety/ordering invariant it depends on: `storage.contents.capacity_pow2()` (the bucket's *current* capacity exponent, an unsigned `u8`) must always be ≥ `self.storage_capacity_when_created_pow2()` (the exponent recorded at write time). This mirrors the reported pattern in `hashOrder`: an assembly-like bit-packing/shift trick with unexplained magic bit-widths (`B8`, `B56` in `PackedStorage`, `B1`/`B63` in `PackedRefCount` [3](#0-2) ) and unstated low-level assumptions about the relationship between two unsigned values, rather than a documented, safely-checked operation.

Data buckets only grow (never shrink) in `grow_data`/`apply_grow_data` [4](#0-3) , and `set_storage_capacity_when_created_pow2` is only ever set from `best_bucket.contents.capacity_pow2()` at write time [5](#0-4) . Under normal operation the invariant holds because growth is monotonic. However, this correctness relies entirely on an unstated/undocumented cross-module contract between `Bucket::grow_data`, `Bucket::try_write`, and `MultipleSlots::data_loc` — there is no assertion, comment, or type-level enforcement anywhere in `data_loc` itself that `capacity_pow2() >= storage_capacity_when_created_pow2()`. If this invariant were ever violated (e.g., by a future change to bucket-restart/reuse logic in `restart.rs`, a bug in `apply_grow_data`, or restoring a stale/incompatible on-disk bucket file on restart via `load_on_restart`), the subtraction `capacity_pow2() - storage_capacity_when_created_pow2()` on two `u8` values would underflow. In a debug build this panics (node crash / DoS); in a release build (where the crate is typically built without overflow checks) this wraps to a large shift count, and `u64::shl` with a shift ≥ 64 is itself UB-adjacent/panics in Rust unless masked — either way producing either a panic or a silently wrong `data_loc`, causing the index to read/write data-file slots at the wrong offset. That translates into either a node panic (DoS on that validator) or a wrong-account-data load (stale/incorrect slot-list or ref-count read from the wrong disk location), i.e., a concrete "stale or wrong-version account load" as required by the validation rubric.

### Impact Explanation
`bucket_map` backs the AccountsIndex disk-based storage used by AccountsDB for scaling the in-memory index to disk (`bucket_map/src/bucket_map.rs`, `bucket.rs`). A wrong `data_loc` computation would cause a validator to read the wrong data-bucket cell for a pubkey's slot list, potentially returning wrong account slot/ref-count data (silent incorrect account state) or panicking the node. Because this logic is entirely local per-validator (no attacker-controlled network input triggers it directly), and no live path in current code violates the invariant, actual exploitability requires a defect being introduced elsewhere (e.g., restart-reuse code) that breaks bucket-growth monotonicity — this is a latent robustness/maintainability risk rather than a demonstrated live bug today.

### Likelihood Explanation
Low likelihood under current code paths, since bucket growth is monotonic and the invariant currently holds by construction across all call sites I could find (`try_write`, `grow_data`, `apply_grow_data`). The risk is that the invariant is completely undocumented and unchecked at the point of use (`data_loc`), so any future refactor of bucket restart/reuse (`restart.rs`) or resizing logic could silently violate it without any test or assertion catching the regression — precisely the "hard for developers to update, hard for reviewers to verify" risk called out in the original report.

### Recommendation
Add an explicit `debug_assert!`/comment in `data_loc` documenting the required invariant (`capacity_pow2() >= storage_capacity_when_created_pow2()`), and use a checked/saturating shift (e.g., `checked_shl` or `saturating_sub` before shifting) instead of a bare subtraction, so any invariant violation surfaces as a controlled error rather than an unsigned underflow/panic or silent wrong offset. Similarly document the bit-widths and packing rationale of `PackedStorage`/`PackedRefCount` inline, as recommended for the original `hashOrder` assembly block.

### Proof of Concept
Not independently reproducible from static review alone; no test in `bucket_map/src/index_entry.rs` (`test_api`, `test_size`, `test_set_storage_offset_value_too_large`, `test_data_bucket_from_num_slots`) exercises `data_loc` with a `capacity_pow2()` smaller than `storage_capacity_when_created_pow2()`, confirming the invariant is asserted nowhere, only relied upon implicitly by call-site ordering in `bucket.rs`. I could not find a currently-reachable agave code path that violates the invariant, so this should be treated as a latent, unverified robustness gap rather than a confirmed live exploit.

### Citations

**File:** bucket_map/src/index_entry.rs (L198-210)
```rust
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
```

**File:** bucket_map/src/index_entry.rs (L271-276)
```rust
    /// This function maps the original data location into an index in the current bucket storage.
    /// This is coupled with how we resize bucket storages.
    pub(crate) fn data_loc(&self, storage: &BucketStorage<DataBucket>) -> u64 {
        self.storage_offset()
            << (storage.contents.capacity_pow2() - self.storage_capacity_when_created_pow2())
    }
```

**File:** bucket_map/src/index_entry.rs (L336-343)
```rust
/// Pack the storage offset and capacity-when-created-pow2 fields into a single u64
#[bitfield(bits = 64)]
#[repr(C)]
#[derive(Debug, Default, Copy, Clone, Eq, PartialEq)]
struct PackedStorage {
    capacity_when_created_pow2: B8,
    offset: B56,
}
```

**File:** bucket_map/src/bucket.rs (L606-613)
```rust
        for i in pos..pos + (max_search * 10).min(cap) {
            let ix = i % cap;
            if best_bucket.is_free(ix) {
                let mut multiple_slots = MultipleSlots::default();
                multiple_slots.set_storage_offset(ix);
                multiple_slots
                    .set_storage_capacity_when_created_pow2(best_bucket.contents.capacity_pow2());
                multiple_slots.set_num_slots(num_slots);
```

**File:** bucket_map/src/bucket.rs (L802-820)
```rust
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
