## Title
Bounded linear-probe search in `Bucket::bucket_find_index_entry` can silently report an existing account key as "not found" - (File: `bucket_map/src/bucket.rs`)

### Summary
The disk-backed accounts-index bucket map performs open-addressing lookups (`bucket_find_index_entry` / `find_index_entry_mut`) with a **bounded** linear probe of only `index.max_search()` slots starting at the pubkey's hashed bucket offset [1](#0-0) . If a key's entry ends up occupying a slot outside that bounded window (which can legitimately happen after repeated inserts/deletes cause probe-sequence clustering, or if the bucket is not correctly resized before the search-limit is exceeded), the lookup returns `None` even though the key is physically present in the bucket. This is structurally analogous to the ERC-1155 finding: a function that is expected to unconditionally succeed/find valid data for any legitimate input instead has a hidden, undocumented failure mode (an artificial boundary condition) that callers of the "standard" lookup interface do not account for.

### Finding Description
`bucket_find_index_entry` and `find_index_entry_mut` both iterate only `index.max_search()` slots from the pubkey-derived starting index (`Self::bucket_index_ix(key, random) % index.capacity()`), wrapping with `% index.capacity()`: [2](#0-1) [1](#0-0) 

`find_index_entry_mut` correctly treats exhaustion of the search window as an *insert-capacity* failure (`BucketMapError::IndexNoSpace`), which triggers a bucket grow via the caller in `bucket_create_key`/insert path [3](#0-2) . However, `bucket_find_index_entry` — used purely for **reads** via `find_index_entry` / `read_value` in `BucketApi` [4](#0-3)  — has no such fallback: if the loop exhausts `max_search` slots without matching the key, it unconditionally returns `None`, which the caller (and `AccountsIndex`) interprets as "key does not exist," not "search window exhausted."

The `Bucket` struct explicitly tracks `at_least_one_entry_deleted`, noting in its own doc-comment that deletions can create the exact clustering condition that requires a full-range search [5](#0-4) , which is direct code evidence that the authors are aware probe distance can legitimately exceed a naive expectation. If this invariant is ever violated (e.g., because of a mismatch between the max_search value used at write time versus at a later read time — the same class of issue that IndexNoSpace already guards against for inserts but that the read-only lookup path does not), an in-disk-index account entry can become permanently "invisible" to lookups performed through the primary `AccountsIndex` for that bucket while the entry is not actually removed from storage.

### Impact Explanation
If `bucket_find_index_entry`/`read_value` returns `None` for a pubkey that is actually present, `AccountsIndex::get`-family calls backed by the disk index would report the account as absent. Depending on the code path that consumes this:
- A validator could treat a live account as non-existent, which for the accounts-index specifically underlies balance/state lookups, hashing during `AccountsDb` scans, and snapshot rebuilding — leading to a stale/incorrect account view (silent divergence from what is actually stored) rather than an explicit, safe error.
- Because this is a false negative in a low-level index primitive rather than a top-level RPC filter, it would not surface as a clean error; it degrades silently in the same spirit as the ERC-1155 bug, where a caller relying on the "always succeeds for a valid, present item" contract gets an unexpected negative result instead.

### Likelihood Explanation
This requires the search-window/clustering edge case described in the `at_least_one_entry_deleted` comment to actually be exercised with a probe distance greater than `max_search`, which the code already anticipates as a real (if rare) scenario for deletions. I was not able to fully trace, within the remaining investigation budget, whether every insert path guarantees the invariant "any live key is always found within `max_search` of its bucket_index_ix" is truly unbreakable (e.g., across bucket resizes/`grow`, on-disk restart reuse via `restart.rs`, or the "OnlyAbnormal" scan filters in `AccountsIndex::scan`). This uncertainty means the concrete trigger condition and blast radius (single-key miss vs. broader corruption) could not be confirmed with full confidence from the code inspected.

### Recommendation
Ensure `bucket_find_index_entry` (and any other read-only lookup path) either (a) provably cannot encounter a live key outside its `max_search` window — by asserting/enforcing that inserts always keep every live key within reach of subsequent reads across all resize/restart code paths — or (b) treats exhaustion of the probe window the same way `find_index_entry_mut` does: as an ambiguous/error condition requiring a wider search or index rebuild, rather than silently returning "not found."

### Proof of Concept
Not independently reproduced; based on static code review of the bounded probe implementation and the codebase's own acknowledgment (via `at_least_one_entry_deleted`) that deletions can push live-key probe distances beyond the naive search window [5](#0-4) , contrasted with the asymmetric handling between the insert path (`find_index_entry_mut`, which raises `BucketMapError::IndexNoSpace` on exhaustion) [6](#0-5)  and the read path (`bucket_find_index_entry`, which silently returns `None`) [1](#0-0) .

### Citations

**File:** bucket_map/src/bucket.rs (L108-110)
```rust
    /// set to true once any entries have been deleted from the index.
    /// Deletes indicate that there can be free slots and that the full search range must be searched for an entry.
    at_least_one_entry_deleted: bool,
```

**File:** bucket_map/src/bucket.rs (L220-257)
```rust
    fn find_index_entry_mut(
        index: &BucketStorage<IndexBucket<T>>,
        key: &Pubkey,
        random: u64,
    ) -> Result<(Option<IndexEntryPlaceInBucket<T>>, u64), BucketMapError> {
        let ix = Self::bucket_index_ix(key, random) % index.capacity();
        let mut first_free = None;
        let mut m = Measure::start("bucket_find_index_entry_mut");
        let capacity = index.capacity();
        for i in ix..ix + index.max_search() {
            let ii = i % capacity;
            if index.is_free(ii) {
                if first_free.is_none() {
                    first_free = Some(ii);
                }
                continue;
            }
            let elem = IndexEntryPlaceInBucket::new(ii);
            if elem.key(index) == key {
                m.stop();

                index
                    .stats
                    .find_index_entry_mut_us
                    .fetch_add(m.as_us(), Ordering::Relaxed);
                return Ok((Some(elem), ii));
            }
        }
        m.stop();
        index
            .stats
            .find_index_entry_mut_us
            .fetch_add(m.as_us(), Ordering::Relaxed);
        match first_free {
            Some(ii) => Ok((None, ii)),
            None => Err(BucketMapError::IndexNoSpace(index.contents.capacity())),
        }
    }
```

**File:** bucket_map/src/bucket.rs (L259-276)
```rust
    fn bucket_find_index_entry(
        index: &BucketStorage<IndexBucket<T>>,
        key: &Pubkey,
        random: u64,
    ) -> Option<(IndexEntryPlaceInBucket<T>, u64)> {
        let ix = Self::bucket_index_ix(key, random) % index.capacity();
        for i in ix..ix + index.max_search() {
            let ii = i % index.capacity();
            if index.is_free(ii) {
                continue;
            }
            let elem = IndexEntryPlaceInBucket::new(ii);
            if elem.key(index) == key {
                return Some((elem, ii));
            }
        }
        None
    }
```

**File:** bucket_map/src/bucket.rs (L278-297)
```rust
    fn bucket_create_key(
        index: &mut BucketStorage<IndexBucket<T>>,
        key: &Pubkey,
        random: u64,
        is_resizing: bool,
    ) -> Result<u64, BucketMapError> {
        let ix = Self::bucket_index_ix(key, random) % index.capacity();
        for i in ix..ix + index.max_search() {
            let ii = i % index.capacity();
            if !index.is_free(ii) {
                continue;
            }
            index.occupy(ii, is_resizing).unwrap();
            // These fields will be overwritten after allocation by callers.
            // Since this part of the mmapped file could have previously been used by someone else, there can be garbage here.
            IndexEntryPlaceInBucket::new(ii).init(index, key);
            return Ok(ii);
        }
        Err(BucketMapError::IndexNoSpace(index.contents.capacity()))
    }
```

**File:** bucket_map/src/bucket_api.rs (L70-77)
```rust
    /// Get the values for Pubkey `key`
    pub fn read_value<C: for<'a> From<&'a [T]>>(&self, key: &Pubkey) -> Option<(C, RefCount)> {
        self.bucket.read().unwrap().as_ref().and_then(|bucket| {
            bucket
                .read_value(key)
                .map(|(value, ref_count)| (C::from(value), ref_count))
        })
    }
```
