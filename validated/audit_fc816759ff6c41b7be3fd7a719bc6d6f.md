### Title
Attacker-driven tombstone churn via account close/reopen forces perpetual full-bin index scans that never reclaim capacity - ([File: accounts-db/src/accounts_index/bucket_map_holder.rs])

### Finding Description
`BucketMapHolder::should_evict_based_on_free_entries` fires whenever a bin's `capacity - len` free entries drop below `overhead = target_entries - high_water_mark`, independently of whether the live entry `count` is anywhere near `high_water_mark`: [1](#0-0) 

This is checked every eligible age by `check_flush_trigger`, which computes `low_free_entries_triggered` from `capacity.saturating_sub(entries_in_bin)` and returns `true` to trigger a full bin scan (`buckets_scanned`, `flush_scan`) even when `should_evict_based_on_count` stays `false`: [2](#0-1) 

`flush_scan` then holds a read lock and iterates the **entire** bin's HashMap (O(bin size)) to gather eviction candidates, bounded by `max_evictions_for_threshold(map.len())`, which is `current_entries.saturating_sub(low_water_mark).max(1)` — i.e. it degenerates to evicting just 1 entry per pass whenever `len` is far below `low_water_mark`: [3](#0-2) [4](#0-3) 

The critical gap: the free-entries condition is driven by hashmap "tombstones" left behind by removed entries. But the only place that clears tombstones, `reallocate_to_clear_tombstones`, is invoked exclusively from `evict_from_cache` after an age-based eviction actually occurs (`evicted > 0`): [5](#0-4) 

However, tombstones are also produced by a completely separate, unprivileged-attacker-reachable path: `InMemAccountsIndex::remove_if_slot_list_empty`, which directly calls `occupied.remove()` on the bin's `HashMap` whenever an account's slot list becomes empty (i.e., the account is closed/reclaimed during clean), with no call to `reallocate_to_clear_tombstones` afterward: [6](#0-5) 

This is reached from `AccountsIndex::handle_dead_keys`, invoked by ordinary account-close cleanup paths (e.g. after `purge_keys_exact` / clean): [7](#0-6) 

An unprivileged user fully controls this: create accounts (paying rent), close them (zero lamports, reclaimed by clean) in a chosen bin (bin selection is a function of pubkey, and pubkeys can be brute-forced/vanity-generated to land in one bin), repeatedly, at a rate that keeps free entries hovering just under `overhead` every age (400 ms in `IndexLimit::Threshold`) while keeping `len` well below `high_water_mark`/`low_water_mark`. Because `remove_if_slot_list_empty`'s tombstones are never reclaimed by `reallocate_to_clear_tombstones` (that only fires from the age-based eviction path with `evicted > 0`), the free-entries condition can be kept perpetually tripped, forcing a full O(bin-size) scan (`flush_scan`, holding the map's read lock) every age interval indefinitely, while `should_evict_based_on_count` never fires and actual capacity reclamation is minimal/absent.

### Impact Explanation
This produces disproportionate CPU cost and lock contention on the accounts index background flush/evict thread relative to the low fee cost of creating/closing small accounts, violating the "cleanup work proportional to fees" invariant. The scoped impact matches the Agave bounty category for wasted CPU cycles / index thrash caused by an unprivileged, low-cost user action, not requiring any privileged role, stake, or leader control.

### Likelihood Explanation
Requires `IndexLimit::Threshold` configuration (a supported, non-default validator index mode). The attacker needs only standard transaction capabilities: create/close accounts they own, and can target a specific index bin by generating pubkeys that hash to that bin (a purely local, unprivileged computation). The trigger condition (`free_entries < overhead`) is a simple counter comparison that is straightforward to hover just below via repeated create/close cycles, and is fully repeatable across ages (every 400 ms) for as long as the attacker sustains the churn.

### Recommendation
Decouple tombstone cleanup from the age-based eviction path: call `reallocate_to_clear_tombstones` (or an equivalent capacity-reclaiming rehash) whenever `remove_if_slot_list_empty` removes an entry and capacity has drifted, or track live/tombstone counts and only trigger `should_evict_based_on_free_entries` scans when the bin's actual entry count also grows meaningfully, so the free-entries trigger cannot be sustained solely by churn from dead-key removal.

### Proof of Concept
Rust unit test plan (extending `test_should_evict_based_on_free_entries_with_threshold_limit` style tests plus `in_mem_accounts_index.rs` test harness):
1. Build a `BucketMapHolder`/`InMemAccountsIndex` with `IndexLimit::Threshold` and small `num_entries_overhead`/`num_entries_to_evict` for fast reproduction (as in `new_should_write_through_for_test`).
2. Insert entries via `map_internal.write().insert(...)` up to just below `high_water_mark`, then repeatedly insert+`remove_if_slot_list_empty` a small rotating set of pubkeys (simulating create/close of accounts) to shrink `capacity - len` below `overhead` without growing `len`.
3. After each churn cycle, call `check_flush_trigger()` and assert it returns `true` while `should_evict_based_on_count(len)` is `false`.
4. Instrument/mock `stats().buckets_scanned` and `stats().num_hashmap_reallocates` counters to assert `buckets_scanned` increases every cycle while `num_hashmap_reallocates` stays at 0 (since no age-based eviction with `evicted > 0` occurs), demonstrating scans without corresponding capacity reclamation.

### Citations

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L130-143)
```rust
    /// Returns true when a bin's HashMap free entries (`capacity - len`) are low
    /// enough that eviction should begin to prevent an imminent capacity doubling.
    /// The threshold is the overhead gap between `target_entries` and `high_water_mark`.
    pub fn should_evict_based_on_free_entries(&self, free_entries: usize) -> bool {
        match &self.threshold_entries_per_bin {
            None => self.is_disk_index_enabled(),
            Some(threshold_entries_per_bin) => {
                let overhead = threshold_entries_per_bin
                    .target_entries
                    .saturating_sub(threshold_entries_per_bin.high_water_mark);
                free_entries < overhead
            }
        }
    }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L145-159)
```rust
    /// Calculate maximum evictions to perform for threshold-based flushing
    /// Returns current_entries for Minimal disk index
    /// Returns the max_evictions for Threshold mode to bring count to the low water mark
    pub fn max_evictions_for_threshold(&self, current_entries: usize) -> NonZeroUsize {
        let evictions = match &self.threshold_entries_per_bin {
            None => current_entries,
            Some(threshold_entries_per_bin) => {
                // Low water mark: evict down to specified ratio of the per-bin threshold
                current_entries.saturating_sub(threshold_entries_per_bin.low_water_mark)
            }
        }
        .max(1);
        // SAFETY: evictions is ensured to be non-zero above.
        NonZeroUsize::new(evictions).unwrap()
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L326-386)
```rust
    /// return false if the entry is in the index (disk or memory) and has a slot list len > 0
    /// return true in all other cases, including if the entry is NOT in the index at all
    fn remove_if_slot_list_empty_entry(
        &self,
        entry: Entry<Pubkey, Box<AccountMapEntry<T>>>,
    ) -> bool {
        match entry {
            Entry::Occupied(occupied) => {
                let result = self
                    .remove_if_slot_list_empty_value(occupied.get().slot_list_lock_read_len() == 0);
                if result {
                    // note there is a potential race here that has existed.
                    // if someone else holds the arc,
                    //  then they think the item is still in the index and can make modifications.
                    // We have to have a write lock to the map here, which means nobody else can get
                    //  the arc, but someone may already have retrieved a clone of it.
                    // account index in_mem flushing is one such possibility
                    self.delete_disk_key(occupied.key());
                    self.stats().dec_mem_count();
                    occupied.remove();
                }
                result
            }
            Entry::Vacant(vacant) => {
                // not in cache, look on disk
                let entry_disk = self.load_from_disk(vacant.key());
                match entry_disk {
                    Some(entry_disk) => {
                        // on disk
                        if self.remove_if_slot_list_empty_value(entry_disk.0.is_empty()) {
                            // not in cache, but on disk, so just delete from disk
                            self.delete_disk_key(vacant.key());
                            true
                        } else {
                            // could insert into cache here, but not required for correctness and value is unclear
                            false
                        }
                    }
                    None => true, // not in cache or on disk, but slot list is 'empty' and entry is not in index, so return true
                }
            }
        }
    }

    // If the slot list for pubkey exists in the index and is empty, remove the index entry for pubkey and return true.
    // Return false otherwise.
    pub fn remove_if_slot_list_empty(&self, pubkey: Pubkey) -> bool {
        let mut m = Measure::start("entry");
        let mut map = self.map_internal.write().unwrap();
        let capacity_pre = map.capacity();
        let entry = map.entry(pubkey);
        m.stop();
        let found = matches!(entry, Entry::Occupied(_));
        let result = self.remove_if_slot_list_empty_entry(entry);
        let capacity_post = map.capacity();
        drop(map);
        self.stats()
            .update_in_mem_capacity(capacity_pre, capacity_post);
        self.update_entry_stats(m, found);
        result
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1101-1123)
```rust
    fn flush_scan(
        &self,
        current_age: Age,
        _flush_guard: &FlushGuard,
        ages_flushing_now: Age,
    ) -> (CandidatesToFlush, CandidatesToEvict) {
        let (possible_evictions, m) = {
            let map = self.map_internal.read().unwrap();
            let m = Measure::start("flush_scan"); // we don't care about lock time in this metric - bg threads can wait
            let max_evictions = self.storage.max_evictions_for_threshold(map.len());
            let possible_evictions = Self::gather_possible_flush_evict_candidates(
                map.iter(),
                current_age,
                ages_flushing_now,
                max_evictions,
                !self.should_write_through,
            );
            (possible_evictions, m)
        };
        Self::update_time_stat(&self.stats().flush_scan_us, m);

        possible_evictions
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1306-1337)
```rust
    fn check_flush_trigger(&self) -> bool {
        let (entries_in_bin, capacity) = {
            let map = self.map_internal.read().unwrap();
            (map.len(), map.capacity())
        };

        // Skip during initial growth: below HWM, low free entries reflect a not-yet-grown
        // table, not tombstones. If tombstones do force a doubling before len crosses HWM,
        // the count check catches it later once len grows past HWM.
        if let Some(thresholds) = &self.storage.threshold_entries_per_bin
            && capacity < thresholds.high_water_mark
        {
            return false;
        }

        let high_count_triggered = self.storage.should_evict_based_on_count(entries_in_bin);
        let low_free_entries_triggered = self
            .storage
            .should_evict_based_on_free_entries(capacity.saturating_sub(entries_in_bin));
        if !high_count_triggered && !low_free_entries_triggered {
            return false;
        }
        if low_free_entries_triggered {
            // Primary case: low free-entry headroom (typically from tombstones).
            Self::update_stat(&self.stats().evict_triggered_by_low_free_entries, 1);
        } else {
            // Backstop: bin is past the high-water mark while free-entry headroom
            // still has slack — typically because the hashmap doubled in size.
            Self::update_stat(&self.stats().evict_triggered_by_high_count, 1);
        }
        true
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1457-1533)
```rust
    /// Rebuild the bin's HashMap into a fresh allocation to clear tombstones left
    /// behind by evictions. hashbrown counts tombstones against `capacity`, so
    /// without this the bin's effective capacity drifts down over time and triggers
    /// the hashmap to double in capacity.
    ///
    /// Only called in Threshold mode, where `capacity >= target_entries` is guaranteed
    /// by the time eviction runs (`check_flush_trigger` gates on `high_water_mark`).
    fn reallocate_to_clear_tombstones(&self) {
        let stats = self.stats();
        let m = Measure::start("reallocate_hashmap");

        let target_entries = self
            .storage
            .threshold_entries_per_bin
            .as_ref()
            .expect("reallocate_to_clear_tombstones only runs in Threshold mode")
            .target_entries;

        let mut map = self.map_internal.write().unwrap();
        let capacity_pre = map.capacity();

        // Drain the old map into a fresh allocation sized to `target_entries` so the
        // backing storage stays stable across eviction cycles. Building a brand-new
        // map (rather than `shrink_to_fit`) guarantees a full rehash, which is what
        // actually clears the tombstones.
        let mut new_map = HashMap::with_capacity_and_hasher(target_entries, map.hasher().clone());
        new_map.extend(map.drain());
        *map = new_map;
        let capacity_post = map.capacity();
        drop(map);

        stats.update_in_mem_capacity(capacity_pre, capacity_post);
        Self::update_stat(&stats.num_hashmap_reallocates, 1);
        Self::update_time_stat(&stats.hashmap_reallocate_us, m);
    }

    // evict keys in 'evictions' from in-mem cache, likely due to age
    fn evict_from_cache(&self, evictions: &[Pubkey], current_age: Age, ages_flushing_now: Age) {
        if evictions.is_empty() {
            return;
        }

        let stats = self.stats();
        let mut failed = 0;
        let mut evicted = 0;
        // chunk these so we don't hold the write lock too long
        for evictions in evictions.chunks(50) {
            let mut map = self.map_internal.write().unwrap();
            let capacity_pre = map.capacity();
            for k in evictions {
                if let Entry::Occupied(occupied) = map.entry(*k) {
                    let v = occupied.get();

                    if v.dirty()
                        || !Self::should_evict_based_on_age(current_age, v, ages_flushing_now)
                    {
                        // marked dirty or bumped in age after we looked above
                        // these evictions will be handled in later passes (at later ages)
                        failed += 1;
                        continue;
                    }

                    // all conditions for eviction succeeded, so really evict item from in-mem cache
                    evicted += 1;
                    occupied.remove();
                }
            }
            let capacity_post = map.capacity();
            drop(map);
            stats.update_in_mem_capacity(capacity_pre, capacity_post);
        }

        // Only Threshold mode cares about tombstone-driven capacity doublings; Minimal
        // evicts everything each pass, so rebuilding every flush is wasted work.
        if evicted > 0 && self.storage.threshold_entries_per_bin.is_some() {
            self.reallocate_to_clear_tombstones();
        }
```

**File:** accounts-db/src/accounts_index.rs (L325-343)
```rust
    /// Remove keys from the account index if the key's slot list is empty.
    /// Returns the keys that were removed from the index.
    ///
    /// When secondary indexes are enabled, callers must pass the returned keys to
    /// `AccountsDb::purge_secondary_indexes_for_dead_keys`, otherwise their secondary index
    /// entries leak.
    #[must_use]
    pub fn handle_dead_keys(&self, dead_keys: &[Pubkey]) -> Vec<Pubkey> {
        let mut pubkeys_removed_from_accounts_index = Vec::default();
        if !dead_keys.is_empty() {
            for key in dead_keys.iter() {
                let w_index = self.get_bin(key);
                if w_index.remove_if_slot_list_empty(*key) {
                    pubkeys_removed_from_accounts_index.push(*key);
                }
            }
        }
        pubkeys_removed_from_accounts_index
    }
```
