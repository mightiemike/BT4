### Title
Panic in `Bucket::occupy_if_matches` when startup index rebuild encounters a stale duplicate pubkey/value pair on disk - (File: bucket_map/src/bucket.rs, bucket_map/src/index_entry.rs)

### Summary
During `AccountsIndex` startup index generation, `in_mem_accounts_index.rs` calls `BucketMap`'s `batch_insert_non_duplicates` / `batch_insert_non_duplicates_reusing_file`, which try to reuse existing on-disk bucket index contents rather than re-write them, for performance. The reuse path (`Bucket::batch_insert_non_duplicates_reusing_file` → `IndexEntryPlaceInBucket::occupy_if_matches`) contains a hard `assert_eq!` that will panic the validator process if it ever finds an occupied disk slot whose key and value match the entry being inserted but whose enum tag is not `Free`.

### Finding Description
The report's bug class is "an ordering/first-match assumption in a duplicate-handling code path causes divergent behavior for what should be an unprivileged, benign case." In `bucket_map/src/index_entry.rs`, `occupy_if_matches` is: [1](#0-0) 

The function assumes that if `key` matches and `data` (the single-slot value) also matches, the slot *must* currently be `Free` — anything else triggers `assert_eq!(enum_tag, OccupiedEnumTag::Free, "index asked to insert the same data twice")`, i.e., an unconditional panic rather than a graceful "found duplicate" classification. This mirrors the AtomicLoans bug class: the code takes for granted a particular ordering/state ("this slot will only be occupied-and-matching if it was freshly initialized as Free") instead of treating "key+value already occupied" uniformly as a duplicate to be reported through the `duplicates` output vector, the way `batch_insert_non_duplicates_internal`'s manual scan path does at: [2](#0-1) 

The test suite explicitly documents and asserts this exact panic condition is reachable: [3](#0-2) 

This "reusing file" path (`batch_insert_non_duplicates_reusing_file`, gated by `reused_file_at_startup`) is invoked from `AccountsIndex`'s in-memory index code during accounts-index bucket-map startup rebuild: [4](#0-3) [5](#0-4) 

The precondition for hitting the panic (as opposed to a `Free`-tag "insert into free slot" success) is that the on-disk bucket file — leftover from a previous run being reused at restart — already contains an *occupied* entry at the computed hash slot with the exact same pubkey and exact same single-element value that startup is trying to (re)insert, but with an `OccupiedEnumTag` other than `Free` (i.e. it wasn't cleared out as expected, e.g. `OneSlotInIndex` for the same key/value pair persisted from a prior run, or a stale `MultipleSlots`/other tag left behind). The code comments elsewhere in this same file acknowledge that "this part of the mmapped file could have previously been used by someone else, there can be garbage here," which is exactly the class of stale/reused disk state that this assert does not defensively handle.

### Impact Explanation
If a validator restarts and reuses its on-disk bucket-map index files at startup (the `reused_file_at_startup` path), and the on-disk file happens to already contain a fully-occupied entry (not `Free`) that matches both key and value of an item currently being re-inserted from the account index rebuild, `occupy_if_matches` panics the process with `"index asked to insert the same data twice"`. This is a node panic triggerable purely by ordinary startup account-index reconstruction state (accounts index reloading from disk), not requiring any malicious peer or crafted snapshot — it only requires that leftover/garbage occupied bucket-file state coincide with a currently-inserted key/value at the same hashed slot, a scenario the surrounding code explicitly anticipates ("garbage here") but this one code path does not tolerate.

### Likelihood Explanation
This requires validator-local disk state (reused bucket-map files across restarts) to contain a specific coincidental collision: same computed slot index, same pubkey, and same single-element value, but with a non-`Free` occupied tag rather than `Free`. This is a narrow, disk-state-dependent condition rather than something a remote or unprivileged actor can trigger arbitrarily, so likelihood is low but the trigger conditions (stale/reused bucket files at startup, standard for a normal validator restart flow) are within normal unprivileged operational scenarios covered by the AccountsDB bucket-map scope.

### Recommendation
Change `occupy_if_matches` to not `assert_eq!`/panic when the tag is non-`Free` but key+value match; instead treat it uniformly as `OccupyIfMatches::FoundDuplicate` (as is already done for the "same key, different value, occupied" case), consistent with the tolerant duplicate-handling used elsewhere in `batch_insert_non_duplicates_internal`. This removes the panic path entirely and lets the caller's existing `duplicates` bookkeeping handle the case safely.

### Proof of Concept
The existing regression test already demonstrates the crash deterministically: [3](#0-2) 

1. Create a blank index file (`create_test_index`).
2. Manually pre-occupy the slot at the computed index for pubkey `k` with the exact value `v` using `entry.init(...)` + `entry.set_slot_count_enum_value(..., OccupiedEnum::OneSlotInIndex(&v))` (simulating a reused-at-startup bucket file that still holds this exact entry from a previous run, i.e. tag = `OneSlotInIndex`, not `Free`).
3. Call `Bucket::<u64>::batch_insert_non_duplicates_reusing_file` with the same `(k, v)` pair, which routes into `occupy_if_matches`.
4. The `assert_eq!(enum_tag, OccupiedEnumTag::Free, "index asked to insert the same data twice")` fires and panics, as the test itself expects (`#[should_panic(expected = "index asked to insert the same data twice")]`).

This confirms the code path is reachable with an in-scope AccountsDB/bucket-map startup-reuse scenario and panics rather than gracefully classifying the entry as a duplicate.

### Citations

**File:** bucket_map/src/index_entry.rs (L410-440)
```rust
    pub(crate) fn occupy_if_matches(
        &self,
        index_bucket: &mut BucketStorage<IndexBucket<T>>,
        data: &T,
        k: &Pubkey,
    ) -> OccupyIfMatches {
        let index_entry = index_bucket.get::<IndexEntry<T>>(self.ix);
        if &index_entry.key == k {
            let enum_tag = index_bucket.contents.get_enum_tag(self.ix);
            if unsafe { &index_entry.contents.single_element } == data {
                assert_eq!(
                    enum_tag,
                    OccupiedEnumTag::Free,
                    "index asked to insert the same data twice"
                );
                index_bucket
                    .contents
                    .set_enum_tag(self.ix, OccupiedEnumTag::OneSlotInIndex);
                OccupyIfMatches::SuccessfulInit
            } else if enum_tag == OccupiedEnumTag::Free {
                // pubkey is same, but value is different, so update value
                self.set_slot_count_enum_value(index_bucket, OccupiedEnum::OneSlotInIndex(data));
                OccupyIfMatches::SuccessfulInit
            } else {
                // found occupied duplicate of this pubkey
                OccupyIfMatches::FoundDuplicate
            }
        } else {
            OccupyIfMatches::PubkeyMismatch
        }
    }
```

**File:** bucket_map/src/bucket.rs (L317-346)
```rust
    /// batch insert of `items`. Assumption is a single slot list element and ref_count == 1.
    /// For any pubkeys that already exist, the index in `items` of the failed insertion and the existing data (previously put in the index) are returned.
    pub(crate) fn batch_insert_non_duplicates(&mut self, items: &[(Pubkey, T)]) -> Vec<(usize, T)> {
        assert!(
            !self.at_least_one_entry_deleted,
            "efficient batch insertion can only occur prior to any deletes"
        );
        let current_len = self.index.count.load(Ordering::Relaxed);
        let anticipated = items.len() as u64;
        self.set_anticipated_count((anticipated).saturating_add(current_len));
        let mut entries = Self::index_entries(items, self.random);
        let mut duplicates = Vec::default();
        let mut entries_created_on_disk = 0;
        // insert, but resizes may be necessary
        loop {
            let cap = self.index.capacity();
            // sort entries by their index % cap, so we'll search over the same spots in the file close to each other
            // `reverse()` is so we can efficiently pop off the end but get ascending order index values
            // sort before calling to make `batch_insert_non_duplicates_internal` easier to test.
            entries.sort_unstable_by(|a, b| (a.0 % cap).cmp(&(b.0 % cap)).reverse());

            let result = Self::batch_insert_non_duplicates_internal(
                &mut self.index,
                &self.data,
                items,
                &mut entries,
                &mut entries_created_on_disk,
                &mut duplicates,
                self.reused_file_at_startup,
            );
```

**File:** bucket_map/src/bucket.rs (L378-426)
```rust
    /// insert every entry in `reverse_sorted_entries` into the index as long as we can find a location where the data in the index
    /// file already matches the data we want to insert for the pubkey.
    /// for every entry that already exists in `index`, add it (and the value already in the index) to `duplicates`
    /// `reverse_sorted_entries` is (raw index (range = U64::MAX) in hash map, index in `items`)
    /// Any entries where the disk couldn't be updated are returned in `reverse_sorted_entries` or `duplicates`.
    /// The remaining items in `reverse_sorted_entries` can be inserted by over-writing non-matchingnew data to the index file.
    pub fn batch_insert_non_duplicates_reusing_file(
        index: &mut BucketStorage<IndexBucket<T>>,
        data_buckets: &[BucketStorage<DataBucket>],
        items: &[(Pubkey, T)],
        reverse_sorted_entries: &mut Vec<(u64, usize)>,
        duplicates: &mut Vec<(usize, T)>,
    ) {
        let max_search = index.max_search();
        let cap = index.capacity();
        let search_end = max_search.min(cap);
        let mut not_found = Vec::default();
        // pop one entry at a time to insert
        'outer: while let Some((ix_entry_raw, ix)) = reverse_sorted_entries.pop() {
            let (k, v) = &items[ix];
            // search for an empty spot starting at `ix_entry`
            for search in 0..search_end {
                let ix_index = (ix_entry_raw + search) % cap;
                let elem = IndexEntryPlaceInBucket::new(ix_index);
                match elem.occupy_if_matches(index, v, k) {
                    OccupyIfMatches::SuccessfulInit => {}
                    OccupyIfMatches::FoundDuplicate => {
                        // pubkey is same, and it is occupied, so we found a duplicate
                        let (v_existing, _ref_count_existing) =
                            elem.read_value(index, data_buckets);
                        // someone is already allocated with this pubkey, so we found a duplicate
                        duplicates.push((ix, *v_existing.first().unwrap()));
                    }
                    OccupyIfMatches::PubkeyMismatch => {
                        // fall through and look at next search value
                        continue;
                    }
                }
                continue 'outer; // this 'insertion' is completed - either because we found a duplicate or we occupied an entry in the file
            }
            // this pubkey did not exist in the file already and we exhausted the search space, so have to try the old way
            not_found.push((ix_entry_raw, ix));
        }
        // now add all entries that were not found
        // they were pushed in order since we popped off input
        // So, to keep them 'reversed', we need to reverse them here.
        // This isn't required for correctness, but fits the optimal iteration order.
        *reverse_sorted_entries = not_found.into_iter().rev().collect();
    }
```

**File:** bucket_map/src/bucket.rs (L480-488)
```rust
                } else {
                    // occupied, see if the key already exists here
                    if elem.key(index) == k {
                        let (v_existing, _ref_count_existing) =
                            elem.read_value(index, data_buckets);
                        duplicates.push((i, *v_existing.first().unwrap()));
                        continue 'outer; // this 'insertion' is completed: found a duplicate entry
                    }
                }
```

**File:** bucket_map/src/bucket.rs (L1076-1106)
```rust
    #[should_panic(expected = "index asked to insert the same data twice")]
    #[test]
    fn test_batch_insert_non_duplicates_reusing_file_insert_twice() {
        let data_buckets = Vec::default();
        let v = 12u64;
        let random = 1;
        // cannot use pubkey [0,0,...] because that matches a zeroed out default file contents.
        let len = 1;
        let raw = (0..len)
            .map(|l| (Pubkey::from([(l + 1) as u8; 32]), v + (l as u64)))
            .collect::<Vec<_>>();

        let mut hashed = Bucket::index_entries(&raw, random);

        let mut index = create_test_index(None);
        let cap = index.capacity();
        let ix = hashed[0].0 % cap;
        let entry = IndexEntryPlaceInBucket::new(ix);
        entry.init(&mut index, &raw[0].0);
        entry.set_slot_count_enum_value(&mut index, OccupiedEnum::OneSlotInIndex(&raw[0].1));

        let mut duplicates = Vec::default();
        // this will assert because the same k,v pair are already occupied in the index.
        Bucket::<u64>::batch_insert_non_duplicates_reusing_file(
            &mut index,
            &data_buckets,
            &raw,
            &mut hashed,
            &mut duplicates,
        );
    }
```
