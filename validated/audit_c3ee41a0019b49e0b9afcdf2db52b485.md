This confirms the analog is reachable: `read_index_for_accessor_or_load_slow` (used by every account load during transaction processing, both readonly and writable, per `accounts-db/src/accounts_db.rs:3532-3545`) calls `self.accounts_index.get_with_and_then(...)`, which ultimately bottoms out in `InMemAccountsIndex::get_internal_inner` at `accounts-db/src/accounts_index/in_mem_accounts_index.rs:268-309`, which calls `get_only_in_mem(pubkey, true, ...)` — the `true` here means `update_age=true`, and inside `get_only_in_mem` (lines 223-258) this triggers `self.set_age_to_future(entry, false)` (line 238), which is defined at lines 260-264:

```
fn set_age_to_future(&self, entry: &AccountMapEntry<T>, is_cached: bool) {
    entry.set_age(self.storage.future_age_to_flush(is_cached));
}
``` [1](#0-0) 

### Title
Cheap repeated account reads indefinitely reset accounts-index LRU age, preventing entries from ever being flushed/evicted from the in-memory index - (File: accounts-db/src/accounts_index/in_mem_accounts_index.rs)

### Summary
The bug-class in the external report is "cheap repeated calls reset a timer/counter used to decide when a costly, delayed operation should occur, prolonging it indefinitely." The equivalent primitive in `agave` is the per-entry `age` field of `AccountMapEntryMeta`, used by the in-memory accounts index (`InMemAccountsIndex`) to decide when an entry is evictable/flushable to the disk-backed bucket map.

### Finding Description
Each accounts-index entry stores an `age` (`accounts-db/src/accounts_index/account_map_entry.rs:219-227`) representing the future age at which it becomes eligible for eviction/flush from the in-memory index bin (an LRU-like scheme). Every time an entry is looked up via `get_only_in_mem(pubkey, update_age=true, ...)`, its age is reset forward to `future_age_to_flush` via `set_age_to_future` (`in_mem_accounts_index.rs:236-238,260-264`). Eviction eligibility is `should_evict_based_on_age` (`in_mem_accounts_index.rs:979-985`), which only evicts entries whose age has fallen behind `current_age` by more than `ages_flushing_now`.

`update_age=true` is exactly the path used for ordinary account reads: `AccountsDb::read_index_for_accessor_or_load_slow` (`accounts_db.rs:3532-3545`) calls `accounts_index.get_with_and_then(...)`, which is implemented via `get_internal_inner` (`in_mem_accounts_index.rs:266-309`), which always calls `get_only_in_mem(pubkey, true, ...)`. This means *any* transaction that merely references an account (read-only or writable, including as a program account, ALT-loaded account, or fee-payer) refreshes that account's age, deferring its eligibility for background flush/eviction from the in-memory bin.

An attacker can therefore keep an arbitrary set of accounts permanently "hot" in the in-memory accounts-index cache by repeatedly submitting cheap transactions (e.g., self-transfers of 1 lamport, or transactions that merely list the target pubkeys as readonly accounts) at a rate faster than the bin's age/eviction interval (`age_ms`, default 2000ms in `Minimal`/`InMemOnly` mode, or 400ms in `Threshold` mode — see `bucket_map_holder.rs:336-356`), preventing `try_make_entry_for_flush`/`evict_from_cache` (`in_mem_accounts_index.rs:994-1051`, `1493-1541`) from ever writing them to disk or reclaiming their in-memory footprint. This is directly analogous to the `distribute(1)` attack that resets `endTimestamp` and stretches the reward-interpolation window in the external report.

### Impact Explanation
This does not corrupt account state, hashes, or capitalization — accounts stay correct in both memory and (eventually) disk. The impact is confined to resource/scheduling behavior: an attacker who repeatedly touches a chosen working set of pubkeys (which can be done extremely cheaply, e.g. self-transfers or "touch" transactions referencing many accounts as readonly per transaction) can keep those entries permanently resident in the in-memory index, working against the size-threshold-based eviction machinery (`should_evict_based_on_count`/`should_evict_based_on_free_entries`, `bucket_map_holder.rs:121-143`) whose entire purpose is to bound memory usage in `Threshold`/`Minimal` disk-index configurations. In `Threshold` mode this can push a bin persistently above its configured `high_water_mark`, since eviction is gated on `should_evict_based_on_age` in addition to the watermark check, and a continuously-refreshed age never satisfies that check — this is a disproportionate, attacker-controlled memory/CPU cost relative to the transaction fees paid, growing with the number of distinct pubkeys touched.

### Likelihood Explanation
The attack is easy: it requires only the ability to submit ordinary, cheap transactions referencing target pubkeys (no privileged role needed), and the age-reset happens on every single account lookup unconditionally. However, the actual severity is bounded because: (1) `future_age_to_flush` is a small, wrapping `u8` window (bounded distance ahead of `current_age`), so a single refresh only buys a modest number of age-ticks (`ages_to_stay_in_cache`, default 5) before the entry can be re-evaluated; (2) the "Minimal"/"InMemOnly" index modes evict unconditionally regardless of watermark, and only the "Threshold" mode's watermark-gated eviction is meaningfully affected; and (3) the effect is a resource/scheduling nuisance (memory footprint / disk-write deferral), not a correctness, hash-divergence, or consensus-safety issue.

### Recommendation
Consider decoupling "keep in cache because still hot" from "indefinitely defer background flush," e.g., by capping how many consecutive age-refreshes an entry may receive before it is forcibly flushed/evicted regardless of continued access, similar to how a maximum aggregate delay would bound the ECG `distribute()` prolongation. Alternatively, ensure the `Threshold` mode's watermark-based eviction can override age-based protection when memory pressure is high, so a busy working set cannot indefinitely block eviction irrespective of access frequency.

### Proof of Concept
Conceptual (adapting the external report's approach):
1. Configure a validator with `IndexLimit::Threshold` accounts-index config (`accounts-db/src/accounts_index/bucket_map_holder.rs:290-333`) with a small memory threshold so watermark-based eviction is exercised.
2. Attacker submits, once every < `age_ms` (400ms in Threshold mode, `bucket_map_holder.rs:336-356`), a cheap transaction (e.g., 1-lamport self-transfer or a transaction listing N target pubkeys as readonly) touching the pubkeys it wants pinned.
3. Each touch traverses `AccountsDb::read_index_for_accessor_or_load_slow` → `get_internal_inner` → `get_only_in_mem(update_age=true)` → `set_age_to_future`, resetting `age` before `should_evict_based_on_age` (`in_mem_accounts_index.rs:979-985`) ever returns true for that entry.
4. As long as the attacker keeps this cadence, the target pubkeys' entries are never selected as flush/evict candidates in `gather_possible_flush_evict_candidates`/`evict_from_cache`, keeping the in-memory index bin's occupied entry count above the configured `high_water_mark` indefinitely. [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L221-264)
```rust
    /// lookup 'pubkey' by only looking in memory. Does not look on disk.
    /// callback is called whether pubkey is found or not
    pub(super) fn get_only_in_mem<RT>(
        &self,
        pubkey: &Pubkey,
        update_age: bool,
        callback: impl for<'a> FnOnce(Option<&'a AccountMapEntry<T>>) -> RT,
    ) -> RT {
        let mut found = true;
        let mut m = Measure::start("get");
        let result = {
            let map = self.map_internal.read().unwrap();
            let result = map.get(pubkey);
            m.stop();

            callback(if let Some(entry) = result {
                if update_age {
                    self.set_age_to_future(entry, false);
                }
                Some(entry)
            } else {
                drop(map);
                found = false;
                None
            })
        };

        let stats = self.stats();
        let (count, time) = if found {
            (&stats.gets_from_mem, &stats.get_mem_us)
        } else {
            (&stats.gets_missing, &stats.get_missing_us)
        };
        Self::update_stat(time, m.as_us());
        Self::update_stat(count, 1);

        result
    }

    /// set age of 'entry' to the future
    /// if 'is_cached', age will be set farther
    fn set_age_to_future(&self, entry: &AccountMapEntry<T>, is_cached: bool) {
        entry.set_age(self.storage.future_age_to_flush(is_cached));
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L266-309)
```rust
    /// lookup 'pubkey' in index (in_mem or disk).
    /// call 'callback' whether found or not
    pub(super) fn get_internal_inner<RT>(
        &self,
        pubkey: &Pubkey,
        // return true if item should be added to in_mem cache
        callback: impl for<'a> FnOnce(Option<&AccountMapEntry<T>>) -> (bool, RT),
    ) -> RT {
        self.get_only_in_mem(pubkey, true, |entry| {
            if let Some(entry) = entry {
                callback(Some(entry)).1
            } else {
                // not in cache, look on disk
                let stats = self.stats();
                let disk_entry = self.load_account_entry_from_disk(pubkey);
                if disk_entry.is_none() {
                    return callback(None).1;
                }
                let disk_entry = disk_entry.unwrap();
                let mut map = self.map_internal.write().unwrap();
                let capacity_pre = map.capacity();
                let entry = map.entry(*pubkey);
                let retval = match entry {
                    Entry::Occupied(occupied) => callback(Some(occupied.get())).1,
                    Entry::Vacant(vacant) => {
                        debug_assert!(!disk_entry.dirty());
                        let (add_to_cache, rt) = callback(Some(&disk_entry));
                        // We are holding a write lock to the in-memory map.
                        // This pubkey is not in the in-memory map.
                        // If the entry is now dirty, then it must be put in the cache or the modifications will be lost.
                        if add_to_cache || disk_entry.dirty() {
                            stats.inc_mem_count();
                            vacant.insert(Box::new(disk_entry));
                        }
                        rt
                    }
                };
                let capacity_post = map.capacity();
                drop(map);
                stats.update_in_mem_capacity(capacity_pre, capacity_post);
                retval
            }
        })
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L979-985)
```rust
    fn should_evict_based_on_age(
        current_age: Age,
        entry: &AccountMapEntry<T>,
        ages_flushing_now: Age,
    ) -> bool {
        current_age.wrapping_sub(entry.age()) <= ages_flushing_now
    }
```

**File:** accounts-db/src/accounts_db.rs (L3532-3545)
```rust
    fn read_index_for_accessor_or_load_slow<'a>(
        &'a self,
        ancestors: &Ancestors,
        pubkey: &'a Pubkey,
        clone_in_lock: bool,
    ) -> Option<(Slot, StorageLocation, Option<LoadedAccountAccessor>)> {
        self.accounts_index
            .get_with_and_then(pubkey, ancestors, true, |(slot, account_info)| {
                let storage_location = account_info.storage_location();
                let account_accessor =
                    clone_in_lock.then(|| self.get_account_accessor(slot, &storage_location));
                (slot, storage_location, account_accessor)
            })
    }
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
