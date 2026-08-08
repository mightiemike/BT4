### Title
Inline eviction linear scan in `get_or_create_index_entry_for_pubkey` allows disproportionate CPU cost via bin-targeted new-account creation - ([File: accounts-db/src/accounts_index/in_mem_accounts_index.rs])

### Summary
When `IndexLimit::Threshold` is configured (`should_write_through == true`), inserting a new pubkey into a bin that is over `high_water_mark` triggers an inline eviction candidate search, `map.iter().find(|(_, v)| !v.dirty())` [1](#0-0) . If the bin's entries are all still dirty (not yet written through to disk), this scan visits the entire bin map without finding an eviction candidate, and that full-map scan repeats on every subsequent insert into the same over-threshold bin.

### Finding Description
`get_or_create_index_entry_for_pubkey` performs the inline-eviction check under the bin's write lock whenever `should_write_through` is true, the bin count exceeds `high_water_mark` (`should_evict_based_on_count`), and the incoming pubkey is not already present: [2](#0-1) 

`should_evict_based_on_count` returns true purely based on `count > high_water_mark` for `IndexLimit::Threshold` [3](#0-2) . Pubkeys are routed to bins by a hash-based `PubkeyBinCalculator` (referenced but not fully inspected in this session — see below), and an unprivileged user fully controls which pubkey a newly-created account uses (by grinding keypairs), so an attacker can generate a set of new-account pubkeys that all land in the same bin.

Newly-created (dirty) entries only become "clean" (eligible for the fast `!v.dirty()` scan match) after an explicit write-through, which happens either synchronously inside `slot_list_mut_with_entry`/`replace` for single-slot, ref-count-1 entries [4](#0-3)  or via an out-of-band `write_through_pubkeys` call [5](#0-4) . Notably, `InMemAccountsIndex::upsert` itself (used for normal account updates) does **not** call `try_write_through`; test code has to invoke `index.try_write_through(pubkey)` explicitly after `upsert` to observe write-through/eviction behavior [6](#0-5) . If, in production, cleanup of dirty entries in a bin lags behind the rate of new inserts into that same bin (e.g. because write-through is batched per-slot rather than per-insert), then for a burst of new accounts targeting the same over-threshold bin within a short window, every insert's `map.iter().find(|(_, v)| !v.dirty())` call will scan the full bin (all dirty) and find nothing, yet still pay the O(n) cost of the scan — and because eviction fails, `n` keeps growing, compounding the cost of each subsequent insert.

This is not caught by any existing guard: `should_evict_based_on_count` only checks total count, not the dirty/clean ratio, and there is no fallback to skip the scan (or bound its cost) when no clean candidate is likely to exist. The debug_assert on line 634-637 is compiled out in release builds and does not protect the hot path anyway.

### Impact Explanation
This is a CPU-cost/DoS concern scoped to a single bin's write-lock-protected linear scan, matching Agave's "disproportionate CPU cost relative to fees paid" bounty category: an attacker paying only standard account-creation/rent fees can force the validator to repeatedly perform O(n) work in the accounts index on the write path, growing with bin occupancy, while the attacker's own transaction cost (compute units) does not reflect this hidden index-side cost. It is confined to `InMemAccountsIndex`'s per-bin write path and does not directly cause stale reads, balance corruption, or hash/capitalization divergence — impact is CPU exhaustion / degraded throughput under Threshold-mode configuration.

### Likelihood Explanation
This requires: (1) the validator operator to run with `IndexLimit::Threshold` configured (a real, documented production mode intended to bound in-memory index size, not merely a bootstrap-only or misconfiguration-only setting), and (2) the attacker to grind keypairs so that many newly created accounts land in the same hash bin, and (3) to submit account-creation transactions fast enough that the background/inline write-through does not keep the bin's dirty ratio low before the next insert arrives. Feasibility of (2) is a fixed, parallelizable one-time cost per targeted bin (standard vanity/grinding cost, inversely proportional to number of bins), and is well precedented in Solana tooling. Feasibility of (3) depends on exact write-through batching cadence relative to per-slot/per-tx account-creation rate, which I was unable to fully trace in the available index (the entry point that calls `write_through_pubkeys` after account stores in `accounts_db.rs` was not located within tool budget). This is the key unresolved uncertainty affecting exploitability confidence.

### Recommendation
Bound the cost of the inline-eviction candidate search independent of dirty/clean ratio, e.g., by capping the number of entries scanned per call (sample a fixed-size subset via reservoir sampling similar to `gather_possible_flush_evict_candidates`, rather than `map.iter().find(...)` unbounded), and by tracking a per-bin "likely all dirty" flag/counter to short-circuit the scan when a full pass previously found no clean entry, avoiding repeated wasted full scans until the next background flush changes the ratio.

### Proof of Concept
Rust unit test plan (extending `in_mem_accounts_index.rs` test module, using `new_should_write_through_for_test`):
```rust
#[test]
fn test_inline_eviction_scan_cost_when_bin_is_all_dirty() {
    // Set a moderate high_water_mark, e.g. (2000, 1000)
    let index = new_should_write_through_for_test(Some((2000, 1000)));
    let slot = 1;
    let info = 1;

    // Fill the bin to just over the threshold WITHOUT calling try_write_through,
    // so all entries remain dirty (simulating a burst of new-account creation
    // faster than background write-through).
    let pubkeys: Vec<_> = (0..2001).map(|_| solana_pubkey::new_rand()).collect();
    for pubkey in &pubkeys {
        let new_value = PreAllocatedAccountMapEntry::new(slot, info, &index.storage, true);
        index.upsert(pubkey, new_value, None, &mut ReclaimsSlotList::new(), UpsertReclaim::IgnoreReclaims);
        // deliberately do NOT call index.try_write_through(pubkey)
    }
    assert_eq!(index.map_internal.read().unwrap().len(), 2001);

    // Now measure the latency of inserting N additional new pubkeys into the
    // same (still all-dirty, over-threshold) bin, each of which triggers
    // get_or_create_index_entry_for_pubkey's inline-eviction scan.
    let start = std::time::Instant::now();
    for _ in 0..100 {
        let pk = solana_pubkey::new_rand();
        let new_value = PreAllocatedAccountMapEntry::new(slot, info, &index.storage, true);
        index.upsert(&pk, new_value, None, &mut ReclaimsSlotList::new(), UpsertReclaim::IgnoreReclaims);
    }
    let elapsed_all_dirty = start.elapsed();

    // Baseline: same experiment but with all bin entries clean (write-through applied),
    // so `map.iter().find` should return almost immediately.
    let index_clean = new_should_write_through_for_test(Some((2000, 1000)));
    for pubkey in &pubkeys {
        let new_value = PreAllocatedAccountMapEntry::new(slot, info, &index_clean.storage, true);
        index_clean.upsert(pubkey, new_value, None, &mut ReclaimsSlotList::new(), UpsertReclaim::IgnoreReclaims);
        index_clean.try_write_through(pubkey);
    }
    let start = std::time::Instant::now();
    for _ in 0..100 {
        let pk = solana_pubkey::new_rand();
        let new_value = PreAllocatedAccountMapEntry::new(slot, info, &index_clean.storage, true);
        index_clean.upsert(&pk, new_value, None, &mut ReclaimsSlotList::new(), UpsertReclaim::IgnoreReclaims);
    }
    let elapsed_clean = start.elapsed();

    // Assert bounded worst-case latency growth vs uniform/clean baseline.
    // A finding is confirmed if elapsed_all_dirty grows super-linearly with bin size
    // and is disproportionately larger than elapsed_clean (e.g. >10x).
    assert!(
        elapsed_all_dirty.as_micros() < elapsed_clean.as_micros() * 10,
        "all-dirty bin scan cost ({:?}) disproportionate vs clean baseline ({:?})",
        elapsed_all_dirty, elapsed_clean
    );
}
```
This test should be parameterized over bin size (fuzzed) to measure whether latency per insert grows linearly (expected, and itself concerning) or worse (compounding due to repeated failed eviction), confirming the O(n) unbounded-scan-per-insert behavior described above. Note: full confirmation of end-to-end exploitability also requires tracing the exact call site in `accounts_db.rs` that invokes `write_through_pubkeys`/`try_write_through` relative to per-slot account processing, which was not fully located in this session.

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L404-431)
```rust
    pub(crate) fn slot_list_mut_with_entry<RT>(
        &self,
        pubkey: &Pubkey,
        user_fn: impl FnOnce(SlotListWriteGuard<T>, &AccountMapEntry<T>) -> RT,
    ) -> Option<RT> {
        let mut write_through_args: Option<(Slot, T)> = None;
        let result = self.get_internal_inner(pubkey, |entry| {
            (
                true,
                entry.map(|entry| {
                    let result = user_fn(entry.slot_list_write_lock(), entry);
                    // always mark dirty unconditionally, even if user_fn made no changes
                    entry.mark_dirty();
                    if self.should_write_through && entry.ref_count() == 1 {
                        let slot_list = entry.slot_list_read_lock();
                        if slot_list.len() == 1 {
                            write_through_args = Some(slot_list[0]);
                        }
                    }
                    result
                }),
            )
        });
        if let Some((slot, account_info)) = write_through_args {
            self.write_through(pubkey, slot, account_info);
        }
        result
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L623-642)
```rust
                // Inline eviction: if at capacity and this pubkey is not already in the map,
                // evict one clean entry to make room before inserting.
                // Only enable when should_write_through is true, as finding a candidate for eviction
                // is expensive when the dirty entries are not being written through
                // This is a rare case; background eviction clears the excess over time.
                if self.should_write_through
                    && self.storage.should_evict_based_on_count(map.len())
                    && !map.contains_key(pubkey)
                {
                    let evict_key = map.iter().find(|(_, v)| !v.dirty()).map(|(k, _)| *k);
                    if let Some(key) = evict_key {
                        debug_assert!(
                            self.load_from_disk(&key).is_some(),
                            "inline eviction target must be on disk"
                        );
                        map.remove(&key);
                        stats.sub_mem_count(1);
                        Self::update_stat(&stats.flush_entries_evicted_from_mem_immediate, 1);
                    }
                }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L2915-2927)
```rust
        // Insert 3 entries via upsert — write-through will clean all of them.
        let initial_pubkeys: Vec<_> = (0..3).map(|_| solana_pubkey::new_rand()).collect();
        for pubkey in &initial_pubkeys {
            let new_value = PreAllocatedAccountMapEntry::new(slot, info, &index.storage, true);
            index.upsert(
                pubkey,
                new_value,
                None,
                &mut ReclaimsSlotList::new(),
                UpsertReclaim::IgnoreReclaims,
            );
            index.try_write_through(pubkey);
        }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L121-128)
```rust
    /// Returns true when a bin's entry count is high enough that eviction should begin.
    /// The threshold is the configured `high_water_mark`.
    pub fn should_evict_based_on_count(&self, count: usize) -> bool {
        match &self.threshold_entries_per_bin {
            None => self.is_disk_index_enabled(),
            Some(threshold_entries_per_bin) => count > threshold_entries_per_bin.high_water_mark,
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L670-680)
```rust
    /// Write through to disk the in-mem entries for `pubkeys`. Each entry is only persisted if it
    /// is dirty, `slot_list.len() == 1`, and `ref_count == 1`. Persisting an entry clears its
    /// dirty flag so it becomes eligible for eviction. No-op when disk index is disabled.
    pub fn write_through_pubkeys(&self, pubkeys: Vec<Pubkey>) {
        if !self.storage.storage.should_write_through() {
            return;
        }
        for pubkey in pubkeys {
            self.get_bin(&pubkey).try_write_through(&pubkey);
        }
    }
```
