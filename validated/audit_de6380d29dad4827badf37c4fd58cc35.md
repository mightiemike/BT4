### Title
Bucket-map index/data storage bounds checks are compiled out in release builds, enabling silent out-of-bounds mmap writes - (`File: bucket_map/src/bucket_storage.rs`)

### Summary
The `BucketStorage<O>` type that backs Agave's on-disk `AccountsIndex` bucket map (used to store per-pubkey slot lists for `AccountsDb`) guards every index-bounds and slice-length invariant with `debug_assert!` instead of a real runtime check. In `occupy`, `free`, `get_start_offset_with_header`, `get_slice`, and `get_slice_mut`, the only protection against `ix >= self.capacity()` or `size > available mmap slice` is a `debug_assert!`, which is compiled to a no-op in release builds — the build profile validators actually run. This mirrors the reported Stylus SDK `memcpy` bug class (an "insufficient" bounds check), except here the check is entirely absent in production, not merely off-by-one.

### Finding Description
`BucketStorage::occupy`, `BucketStorage::free`, and `BucketStorage::get_start_offset_with_header` all validate `ix < self.capacity()` only via `debug_assert!`: [1](#0-0) 

Those computed offsets then feed directly into `get_slice`/`get_slice_mut`, which build a raw slice into the memory-mapped file using `unsafe { std::slice::from_raw_parts_mut(ptr, len) }`, again only `debug_assert!`-checking that the underlying `mmap` slice is at least `size` bytes: [2](#0-1) 

These primitives are used throughout `Bucket::try_write` (`bucket_map/src/bucket.rs`) to write index entries and slot-list data for accounts into the mmap'd index/data buckets, e.g. `current_bucket.get_slice_mut(elem_loc, data_len as u64, IncludeHeader::NoHeader)`: [3](#0-2) 
and `best_bucket.get_slice_mut(ix, num_slots, IncludeHeader::NoHeader)` when allocating a new slot for a key during index growth/insert: [4](#0-3) 

Because the enforcement of `ix < capacity` and `len <= remaining mmap bytes` is stripped out in release builds, any code path that computes an `ix` or `data_len`/`num_slots` inconsistent with the bucket's actual allocated capacity (e.g., due to a stale `capacity` read across a concurrent resize, an arithmetic error in `data_bucket_from_num_slots`, or a future regression) will not panic safely — it will silently produce a slice pointing past the end of the `mmap`, and the subsequent write (`slice.iter_mut().zip(data).for_each(...)`) corrupts adjacent memory or hits a segfault, rather than failing a checked bounds test as the code comments imply.

### Impact Explanation
`BucketStorage` underlies the disk-backed `AccountsIndex` bucket map, which stores the pubkey→slot-list mapping used by `AccountsDb` for every stored account. A silent out-of-bounds write here can corrupt adjacent index/data bucket entries (other pubkeys' slot lists, ref-counts, or occupied bitmaps) without any error being raised, leading to wrong-version/stale account loads, incorrect ref-count/slot-list state (silent balance/authority divergence in subsequent lookups), or a hard validator crash if the out-of-bounds region falls outside the mapped page.

### Likelihood Explanation
The invariant is only enforced in `debug_assert!` form, so it provides zero protection in the release binaries that validators actually run (`agave-validator` is built in release mode). Exploitation does not require a malicious peer or privileged role — any workload that drives sufficient account/index churn (many pubkeys, high slot-list growth, frequent index resizes as described in `BucketMap`'s "mostly contention free concurrent" design) exercises these code paths at the volumes where a latent capacity/size mismatch would matter. The concrete trigger condition (a specific `ix`/`capacity` desync during a resize race, or a `data_len` computation bug) is not proven to be currently reachable from any call site in this snapshot, so likelihood should be treated as depending on an as-yet-unidentified invariant violation rather than a demonstrated concrete exploit.

### Recommendation
Replace the `debug_assert!` calls guarding `ix < self.capacity()` in `occupy`, `free`, and `get_start_offset_with_header`, and the `slice.len() >= size` check in `get_slice`/`get_slice_mut` (`bucket_map/src/bucket_storage.rs`), with real runtime checks (`assert!` or a `Result`-returning API) so that out-of-bounds access is a controlled panic/error in all build profiles rather than undefined behavior in release. Add fuzz/property tests that exercise bucket resize races and boundary `data_len`/`num_slots` values to catch regressions early, consistent with the long-term recommendation in the original report to test edge-case inputs.

### Proof of Concept
No concrete PoC could be constructed from static review alone: all current call sites in `bucket_map/src/bucket.rs` compute `ix` via modulo against `cap` (`bucket.rs` lines 606-608) or via `find_index_entry_mut`, which by construction stay within capacity, so the debug_assert-protected invariant is not observably violated by any code path found in this repository snapshot. The concern is that the *safety net* for that invariant is absent in release builds, so any future or concurrency-induced violation (e.g., stale capacity read during `grow_index`'s reallocation, seen at `bucket_map/src/bucket.rs` lines 685-747) degrades from a safe panic into silent memory corruption.

### Citations

**File:** bucket_map/src/bucket_storage.rs (L288-311)
```rust
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

**File:** bucket_map/src/bucket_storage.rs (L345-388)
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

    pub(crate) fn get_slice_mut<T>(
        &mut self,
        ix: u64,
        len: u64,
        header: IncludeHeader,
    ) -> &mut [T] {
        // If the caller is including the header, then `len` *must* be 1
        debug_assert!(
            (header == IncludeHeader::NoHeader) || (header == IncludeHeader::Header && len == 1)
        );
        let start = self.get_start_offset(ix, header);
        let slice = {
            let size = std::mem::size_of::<T>() * len as usize;
            let slice = &mut self.mmap[start..];
            debug_assert!(slice.len() >= size);
            &mut slice[..size]
        };
        let ptr = {
            let ptr = slice.as_mut_ptr().cast();
            debug_assert!((ptr as usize).is_multiple_of(std::mem::align_of::<T>()));
            ptr
        };
        unsafe { std::slice::from_raw_parts_mut(ptr, len as usize) }
    }
```

**File:** bucket_map/src/bucket.rs (L570-581)
```rust
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
