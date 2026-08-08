### Title
Missing bounds check in `BucketStorage::get_slice`/`get_slice_mut` relies on `debug_assert!`, allowing out-of-bounds mmap access in the on-disk accounts index bucket map - (File: `bucket_map/src/bucket_storage.rs`)

### Summary
`BucketStorage::get_slice` and `get_slice_mut`, the low-level accessors used to read/write entries in the disk-backed `AccountsIndex` bucket map, validate that the requested byte range fits within the backing mmap only via `debug_assert!`, which is compiled out in release builds. This mirrors the bitcoin-spv analog: a length that should be validated before being used to slice a buffer is instead only checked incompletely (there, not at all; here, only in debug builds), so in production the invariant is unenforced and any caller that supplies an inconsistent `len`/`ix` combination silently reads or writes outside the intended cell instead of failing loudly.

### Finding Description
`get_slice`/`get_slice_mut` compute `start` from `ix` and the bucket's `cell_size`, then slice `size = size_of::<T>() * len` bytes out of `&self.mmap[start..]`: [1](#0-0) 

The only protection against `start + size` exceeding the mmap length is `debug_assert!(slice.len() >= size)`, which is stripped in `--release` builds. All higher-level operations that read or write the disk index's slot lists funnel through these functions using a `len`/`num_slots` value that is not re-validated against the bucket's actual capacity/cell size at the call site, e.g. `IndexEntryPlaceInBucket::read_value` uses `multiple_slots.num_slots` to size the read: [2](#0-1) 

and `Bucket::try_write` uses a caller-supplied `data_len`/`num_slots` to size the write into the data bucket via `get_slice_mut`: [3](#0-2) [4](#0-3) 

If `num_slots` (derived from the account's slot list length/ref count) and the actual bucket capacity/cell size ever become inconsistent — e.g., through a bug in bucket growth/resizing (`copy_contents`/`grow`), a restart/reload path (`BucketStorage::load_on_restart`) that recomputes `num_elems` from file size and `elem_size`, or an off-by-one in `data_bucket_from_num_slots` bucket selection — the release build has no safety net: it will silently read/write past the end of the intended cell (and potentially past the mmap) rather than panicking. Because `is_free`, `occupy`, `free`, and `get_start_offset_with_header` themselves also only use `debug_assert!` for their capacity checks, the entire read/write path for the AccountsIndex disk bucket map depends on debug-only assertions to prevent misaligned/out-of-range access: [5](#0-4) 

This is the closest reachable in-scope analog to the bitcoin-spv issue: a length parameter that is trusted without a runtime-enforced check, which can propagate incorrect slot-list data into the accounts index used by `AccountsDb`.

### Impact Explanation
If the length/index invariant is ever violated (e.g., due to a latent bucket-resize or restart-reload bug), the consequence in a release binary is not a clean panic but a silent out-of-bounds mmap read (returning garbage/adjacent-cell bytes as a slot-list entry — potentially a wrong `(slot, AccountInfo)` pair, corrupting `AccountsIndex` lookups and causing stale/wrong account loads or divergent capitalization/hash) or an out-of-bounds mmap write (corrupting neighboring index/data cells on disk). Both outcomes affect the on-disk accounts index that `AccountsDb` relies on for account lookups, matching the "concrete stale or wrong-version account load / silent balance change / hash divergence" impact classes in scope. Because this is a validator-internal storage structure (not attacker-supplied wire data), the trigger would have to come from an internal inconsistency (resize/restart logic), not directly from external input — so the severity is contingent on such an inconsistency existing or being introduced, rather than being a directly attacker-triggerable path today.

### Likelihood Explanation
Likelihood is low-to-moderate: the code paths that produce `len`/`ix`/`num_slots` values are internally computed by the bucket map itself (capacity growth in `copy_contents`, `data_bucket_from_num_slots`, and restart reload sizing in `load_on_restart`), and are not directly attacker-controlled. However, because the safety net is only a `debug_assert!` (removed in production release builds), any future refactor, off-by-one, or resize/restart edge case in this arithmetic would silently corrupt data instead of failing fast, making regressions in this area far more dangerous and harder to detect than they would be with a hard `assert!`.

### Recommendation
Replace the bounds/alignment `debug_assert!` checks in `BucketStorage::get_slice`, `get_slice_mut`, `get_start_offset_with_header`, `occupy`, and `free` with `assert!` (or return a `Result`/error) so that any invariant violation fails loudly in production rather than silently reading/writing out of bounds. At minimum, the size/bounds check in `get_slice`/`get_slice_mut` (`slice.len() >= size`) should be a hard runtime check, since it directly guards against out-of-bounds mmap access.

### Proof of Concept
Not directly constructible from static analysis alone: reproducing the out-of-bounds access requires driving `BucketStorage` into a state where `ix`/`len` exceeds the mmap's actual capacity (e.g., via a resize/restart-reload arithmetic bug), which was not verified to be currently reachable through normal `AccountsIndex` operation in this review. The concrete risk demonstrated is structural: `get_slice`/`get_slice_mut` in `bucket_map/src/bucket_storage.rs:345-388` perform the critical bounds check only via `debug_assert!`, which `cargo build --release` compiles out, so no runtime protection exists in production if any caller ever violates the invariant.

### Citations

**File:** bucket_map/src/bucket_storage.rs (L286-311)
```rust
    /// 'is_resizing' true if caller is resizing the index (so don't increment count)
    /// 'is_resizing' false if caller is adding an item to the index (so increment count)
    pub fn occupy(&mut self, ix: u64, is_resizing: bool) -> Result<(), BucketStorageError> {
        debug_assert!(ix < self.capacity(), "occupy: bad index size");
        //debug!("ALLOC {} {}", ix, uid);
        if self.try_lock(ix) {
            if !is_resizing {
                self.count.fetch_add(1, Ordering::Relaxed);
            }
            Ok(())
        } else {
            Err(BucketStorageError::AlreadyOccupied)
        }
    }

    pub fn free(&mut self, ix: u64) {
        debug_assert!(ix < self.capacity(), "bad index size");
        let start = self.get_start_offset_with_header(ix);
        self.contents.free(&mut self.mmap[start..], ix as usize);
        self.count.fetch_sub(1, Ordering::Relaxed);
    }

    fn get_start_offset_with_header(&self, ix: u64) -> usize {
        debug_assert!(ix < self.capacity(), "bad index size");
        (self.cell_size * ix) as usize
    }
```

**File:** bucket_map/src/bucket_storage.rs (L345-363)
```rust
    pub(crate) fn get_slice<T>(&self, ix: u64, len: u64, header: IncludeHeader) -> &[T] {
        // If the caller is including the header, then `len` *must* be 1
        debug_assert!(
            (header == IncludeHeader::NoHeader) || (header == IncludeHeader::Header && len == 1)
        );
        let start = self.get_start_offset(ix, header);
        let slice = {
            let size = std::mem::size_of::<T>() * len as usize;
            let slice = &self.mmap[start..];
            debug_assert!(slice.len() >= size);
            &slice[..size]
        };
        let ptr = {
            let ptr = slice.as_ptr().cast();
            debug_assert!((ptr as usize).is_multiple_of(std::mem::align_of::<T>()));
            ptr
        };
        unsafe { std::slice::from_raw_parts(ptr, len as usize) }
    }
```

**File:** bucket_map/src/index_entry.rs (L457-467)
```rust
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
```

**File:** bucket_map/src/bucket.rs (L565-581)
```rust
            if best_fit_bucket == bucket_ix as u64 {
                // in place update in same data file
                MultipleSlots::set_ref_count(current_bucket, elem_loc, ref_count);

                // write data
                assert!(!current_bucket.is_free(elem_loc));
                let slice: &mut [T] = current_bucket.get_slice_mut(
                    elem_loc,
                    data_len as u64,
                    IncludeHeader::NoHeader,
                );
                multiple_slots.set_num_slots(num_slots);

                slice.iter_mut().zip(data).for_each(|(dest, src)| {
                    *dest = *src;
                });
                return Ok(());
```

**File:** bucket_map/src/bucket.rs (L606-625)
```rust
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
```
