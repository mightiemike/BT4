## Title
Unprivileged accounts can force out arbitrary "clean" entries from the in-memory accounts-index (no recency ordering), causing disproportionate reload/CPU cost for legitimately hot accounts under bounded index-memory configurations - (`File: accounts-db/src/accounts_index/in_mem_accounts_index.rs`)

### Summary
When a validator runs with a bounded `IndexLimit::Threshold` accounts-index memory limit (`--accounts-index-limit <SIZE>`), `InMemAccountsIndex` performs **inline eviction** on every insert of a brand-new pubkey once a bin is at capacity. Unlike the background flush/evict path (which is age/LRU-ordered), the inline-eviction candidate is chosen with `map.iter().find(|(_, v)| !v.dirty())` — i.e. the first "clean" entry encountered in the HashMap's arbitrary iteration order, with **no recency or "hotness" criterion at all**. This is directly analogous to the CLOB bug: a bounded, per-bin data structure evicts an entry chosen by a weak/arbitrary rule whenever a cheap, newly-created item is inserted, letting an unprivileged actor push out entries that legitimate, frequently-accessed workloads depend on.

### Finding Description
`get_or_create_index_entry_for_pubkey` is the only path (under `should_write_through`, i.e. `IndexLimit::Threshold` mode) that performs eviction synchronously at insert time: [1](#0-0) 

The comment explicitly documents that this eviction is opportunistic rather than principled: "Inline eviction: if at capacity and this pubkey is not already in the map, evict one clean entry to make room before inserting." The candidate is simply the first clean (non-dirty, i.e. already written through to disk) entry found by HashMap iteration order — it has no relationship to how recently or frequently that entry has been accessed. Contrast this with the background flush/evict scan, which is age-gated via `should_evict_based_on_age`/`ages_to_stay_in_cache`: [2](#0-1) 

Because `should_evict_based_on_count`/`should_evict_based_on_free_entries` trigger purely on bin occupancy relative to `high_water_mark`/free-entry headroom (configured via `AccountsDbConfig`/`AccountsIndexConfig`), any unprivileged user who repeatedly causes writes to brand-new, never-before-seen pubkeys within a bin can keep that bin pinned at its high-water mark, continuously triggering the arbitrary inline eviction on every subsequent new insert: [3](#0-2) 

This is confirmed by the existing test that documents the behavior as "evict one entry" without any LRU/hotness guarantee: [4](#0-3) 

The mirror-image, principled mechanism — sampled LRU eviction used by `ReadOnlyAccountsCache` — explicitly picks the *oldest* accessed entry from a random sample and is empirically shown (via `test_read_only_accounts_cache_eviction`) to avoid evicting recently-used ("hot") data with high probability: [5](#0-4) 

The in-memory accounts-index's inline path has no equivalent protection for the entries it evicts.

### Impact Explanation
Any entry evicted this way is removed from the fast in-memory index and must be reloaded from the on-disk `BucketMap` on the next access. If the entry evicted belongs to a "hot" account (e.g., a frequently invoked program, a popular token mint, or any account repeatedly touched by many transactions), every subsequent transaction that touches it pays the cost of a disk-backed index lookup (`load_from_disk`) instead of a cheap in-memory hit, on every validator running with a bounded index-memory configuration. An unprivileged user can sustain this by repeatedly submitting transactions that create/touch large numbers of brand-new pubkeys within the same accounts-index bins the hot accounts hash into, keeping those bins pinned at the high-water mark and continuously forcing inline evictions of arbitrary clean entries. This produces disproportionate CPU and disk I/O cost across the whole network relative to the cost paid by the attacker, which falls within the "disproportionate storage and CPU cost" impact category.

### Likelihood Explanation
This requires the validator to be configured with a bounded accounts-index memory limit (`IndexLimit::Threshold`, e.g., `--accounts-index-limit 50GB`) rather than the CLI default of `unlimited`/`InMemOnly`: [6](#0-5) 

Large validators running with limited RAM commonly enable such thresholds intentionally to bound index memory, so this is a realistic production configuration, not a contrived one. Triggering the condition only requires ordinary, unprivileged transactions that touch many distinct new pubkeys — no special privilege is needed, mirroring the "any user, cheap orders" nature of the original CLOB report.

### Recommendation
Replace the arbitrary `map.iter().find(|(_, v)| !v.dirty())` selection in `get_or_create_index_entry_for_pubkey` with a selection strategy that accounts for recency/hotness (e.g., reuse the same `Age`/`ages_to_stay_in_cache` bookkeeping already used by the background flush/evict path, or sample multiple candidates and pick the oldest, similar to `ReadOnlyAccountsCache::evict`). This ensures that a burst of inserts for brand-new, cold pubkeys cannot cheaply and repeatedly evict genuinely hot in-memory index entries.

### Proof of Concept
1. Start a validator with `--accounts-index-limit` set to a bounded value (enabling `IndexLimit::Threshold` and thus `should_write_through = true`).
2. Repeatedly submit transactions from an unprivileged account that create/write many distinct new pubkeys until several accounts-index bins reach their `high_water_mark` (see `test_should_evict_based_on_count_with_threshold_limit` for how the threshold is computed).
3. Continue submitting new-pubkey writes into the same bins; each triggers `get_or_create_index_entry_for_pubkey`'s inline-eviction branch, which evicts the first clean entry found by hashmap iteration order (as demonstrated by `test_inline_eviction_when_bin_exceeds_threshold`).
4. Observe that a hot, frequently-accessed account whose entry happens to be iterated first gets evicted from memory, and subsequent legitimate accesses to it incur `load_from_disk` reads instead of memory hits, at a rate the attacker fully controls by continuing to submit cheap new-pubkey writes.

### Citations

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

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1310-1337)
```rust
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

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L2953-2977)
```rust
        // Inline eviction removes one entry before inserting the new one, leaving the bin count at 3
        assert_eq!(index.map_internal.read().unwrap().len(), 3);

        // The new pubkey must be present in memory.
        let mut found = None;
        index.get_only_in_mem(&new_pubkey, false, |entry| found = Some(entry.is_some()));
        assert_eq!(
            found,
            Some(true),
            "newly inserted entry should be in memory"
        );

        // Exactly one of the original entries was evicted from memory (but remains on disk).
        let evicted_count = initial_pubkeys
            .iter()
            .filter(|pubkey| {
                let mut in_mem = false;
                index.get_only_in_mem(pubkey, false, |entry| in_mem = entry.is_some());
                !in_mem
            })
            .count();
        assert_eq!(
            evicted_count, 1,
            "exactly one original entry should have been evicted"
        );
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L121-143)
```rust
    /// Returns true when a bin's entry count is high enough that eviction should begin.
    /// The threshold is the configured `high_water_mark`.
    pub fn should_evict_based_on_count(&self, count: usize) -> bool {
        match &self.threshold_entries_per_bin {
            None => self.is_disk_index_enabled(),
            Some(threshold_entries_per_bin) => count > threshold_entries_per_bin.high_water_mark,
        }
    }

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

**File:** accounts-db/src/read_only_accounts_cache.rs (L349-397)
```rust
    /// Evicts entries until the cache's size is <= `target_data_size`,
    /// following the sampled LRU eviction method, where a sample of size
    /// `evict_sample_size` is randomly selected from the cache, using the
    /// provided `rng`.
    ///
    /// Returns the number of entries evicted.
    fn evict<R>(
        target_data_size: usize,
        data_size: &AtomicUsize,
        cache_len: &AtomicUsize,
        evict_sample_size: usize,
        cache: &DashMap<ReadOnlyCacheKey, ReadOnlyAccountCacheEntry, AHashRandomState>,
        rng: &mut R,
        #[cfg(feature = "dev-context-only-utils")] mut callback: impl FnMut(
            &Pubkey,
            Option<ReadOnlyAccountCacheEntry>,
        ),
    ) -> u64
    where
        R: Rng,
    {
        let mut num_evicts: u64 = 0;
        while data_size.load(Ordering::Relaxed) > target_data_size {
            let mut key_to_evict = None;
            let mut min_update_time = u64::MAX;
            let mut remaining_samples = evict_sample_size;
            // NOTE: This can loop indefinitely if the cache is misconfigured
            // and when we get here there aren't at least `evict_sample_size`
            // elements. We could break the loop on `cache.is_empty()` but
            // calling `is_empty()` and `len()` on a dashmap is very expensive
            // as it requires iterating and locking all the shards. So, avoid
            // paying that cost and assume that when eviction triggers the
            // cache contains enough items.
            while remaining_samples > 0 {
                let shard = cache
                    .shards()
                    .choose(rng)
                    .expect("number of shards should be greater than zero");
                let shard = shard.read();
                for (key, entry) in shard.iter().choose_multiple(rng, remaining_samples) {
                    let last_update_time = entry.get().last_update_time.load(Ordering::Relaxed);
                    if last_update_time < min_update_time {
                        min_update_time = last_update_time;
                        key_to_evict = Some(key.to_owned());
                    }

                    remaining_samples = remaining_samples.saturating_sub(1);
                }
            }
```

**File:** validator/src/commands/run/args.rs (L1043-1067)
```rust
    .arg(
        Arg::with_name("accounts_index_limit")
            .long("accounts-index-limit")
            .value_name("VALUE")
            .takes_value(true)
            .possible_values(&[
                "minimal",
                "25GB",
                "50GB",
                "100GB",
                "200GB",
                "400GB",
                "800GB",
                "unlimited",
            ])
            .default_value("unlimited")
            .help("Sets the memory limit for the accounts index")
            .long_help(
                "Sets the memory limit for the accounts index. The size options will limit the \
                 accounts index memory to the specified value. E.g. \"50GB\" means the accounts \
                 index may use up to 50 GB of memory. The \"unlimited\" option keeps the entire \
                 accounts index in memory. All index entries that are not in memory are kept in \
                 the disk-backed index. The disk-backed index has lower performance; prefer \
                 higher explicit limits here.",
            ),
```
