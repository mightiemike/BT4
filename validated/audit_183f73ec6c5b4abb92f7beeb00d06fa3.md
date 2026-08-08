### Title
Every account read resets its index entry's eviction age, letting hot pubkeys pin themselves in the in-memory `AccountsIndex` bin indefinitely - ([File: accounts-db/src/accounts_index/in_mem_accounts_index.rs])

### Summary
The GMX report's root cause is that a routine "touch" operation (topping up the execution fee) unconditionally resets a validity timestamp (`order.touch()`), so the more an order is touched under load, the further its execution window keeps sliding forward — and the operator has no way to update state without resetting that timer. The agave analog is in the disk-backed accounts index: every in-memory lookup of an `AccountMapEntry` unconditionally pushes the entry's `age` field into the future via `set_age_to_future`, deferring the entry's eligibility for eviction/flush to disk, with no bound on how often this can happen.

### Finding Description
`InMemAccountsIndex::get_only_in_mem` takes an `update_age: bool` parameter; when true (the default path used by `get_internal_inner`, which backs ordinary index lookups such as account loads), it calls `self.set_age_to_future(entry, false)` on every hit: [1](#0-0) 

`set_age_to_future` simply overwrites the entry's `age` meta field to `storage.future_age_to_flush(is_cached)`, i.e. pushes the entry's eviction eligibility further into the future, exactly like `order.touch()` unconditionally bumping the update timestamp in the GMX code: [2](#0-1) 

Eviction/flush eligibility is gated purely on this `age` value relative to `current_age`: [3](#0-2) 

and the flush/eviction scan (`gather_possible_flush_evict_candidates`) filters out any entry whose age was recently bumped: [4](#0-3) 

The `BucketMapHolder` is explicitly sized under the assumption that only a bounded number of entries are dirty/hot at once (`DEFAULT_NUM_ENTRIES_OVERHEAD`/`DEFAULT_NUM_ENTRIES_TO_EVICT`, `ThresholdEntriesPerBin` high/low water marks): [5](#0-4) [6](#0-5) 

Because `get_only_in_mem(update_age=true)` is on the ordinary read path (any transaction, RPC `getAccountInfo`, program account access, etc. touching a pubkey), an unprivileged user can keep any set of pubkeys "hot" simply by repeatedly reading them (e.g., during a burst of transactions after network congestion clears, analogous to the "gas spike after outage" surge described in the GMX report, when queued transactions all land at once). Just like the GMX order that keeps getting "touched" and never becomes stale enough to execute at the intended price, these accounts-index entries keep getting "touched" and never become stale enough to be evicted, defeating the age-based LRU that the `Threshold` disk-index mode relies on to bound in-memory index size.

### Impact Explanation
In `IndexLimit::Threshold` mode (memory-bounded disk index), the design assumes a bounded number of entries can be "recently touched" at any time and get flushed/evicted as they age out, keeping the in-mem HashMap near `target_entries` per bin. If a burst of ordinary account reads (post-outage backlog, hot program accounts, etc.) continuously refreshes the `age` on a large, sustained working set, entries never age out for eviction, causing sustained above-target in-memory index growth and repeated `check_flush_trigger`/scan cycles that fail to make progress — disproportionate memory and CPU cost relative to the configured `IndexLimitThreshold::num_bytes` budget. This matches the accepted "disproportionate storage and CPU cost" impact category. It does not corrupt state or cause silent balance changes; it is a resource-accounting/DoS-adjacent effect on an honest node's own bookkeeping.

### Likelihood Explanation
This requires no privileged access — any client repeatedly reading the same set of pubkeys (a burst of transactions or RPC calls, which is exactly the kind of "spike after an outage" scenario described in the GMX report where a backlog of transactions all target the same hot accounts once the network recovers) is sufficient to trigger sustained age refresh. It is somewhat mitigated because `Threshold` mode also does eviction based on the `low_water_mark`/`high_water_mark` regardless of "should evict" logic reconsidering — but the core age-check logic still filters by `should_evict_based_on_age`, so if the working set size legitimately exceeds thresholds, growth beyond intended limits is plausible under sustained hot-key access.

### Recommendation
Since contract-level analog would be: cap how far `set_age_to_future` can push an entry's eligibility forward relative to how recently it was already bumped (i.e., don't let read-driven age refresh perpetually starve the eviction scan for a bounded pool of "hot" entries), or decouple "age" freshness for eviction purposes from plain reads versus writes, similarly to how the GMX recommendation separates "touch that must reset validity" from "touch that should not." At minimum, ensure the `Threshold` mode's `should_evict_based_on_count`/`should_evict_based_on_free_entries` checks (hard byte/count limits) are always enforced independent of the age-based soft eviction so that a sustained hot working set cannot exceed the configured memory budget even if age keeps getting bumped.

### Proof of Concept
Not independently reproduced/benchmarked; this is inferred from static analysis of `get_only_in_mem`/`set_age_to_future`/`gather_possible_flush_evict_candidates` and the `ThresholdEntriesPerBin` sizing assumptions. A concrete PoC would require running with `IndexLimit::Threshold` configured and driving sustained concurrent reads (e.g. via banking-stage transaction replay or repeated `get_account`) against a working set sized near/above `target_entries_per_bin` while observing `stats().num_hashmap_reallocates` / mem_count growth beyond the high water mark; I was not able to execute this in the current environment.

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L221-246)
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
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L260-264)
```rust
    /// set age of 'entry' to the future
    /// if 'is_cached', age will be set farther
    fn set_age_to_future(&self, entry: &AccountMapEntry<T>, is_cached: bool) {
        entry.set_age(self.storage.future_age_to_flush(is_cached));
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

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L1053-1090)
```rust
    /// Collect candidates to flush/evict from `iter` by checking age
    /// Skip entries with ref_count != 1 since they will be rejected later anyway
    fn gather_possible_flush_evict_candidates<'a>(
        iter: impl Iterator<Item = (&'a Pubkey, &'a Box<AccountMapEntry<T>>)>,
        current_age: Age,
        ages_flushing_now: Age,
        max_evictions: NonZeroUsize,
        collect_flush_candidates: bool,
    ) -> (CandidatesToFlush, CandidatesToEvict) {
        let mut candidates_to_flush = Vec::new();
        let mut rng = rng();
        // use reservoir sampling to select a bounded, roughly uniform subset
        let mut sampling_state = ReservoirState {
            samples: Vec::with_capacity(max_evictions.get()),
            seen: 0,
            max_samples: max_evictions,
        };
        for (k, v) in iter {
            if !Self::should_evict_based_on_age(current_age, v, ages_flushing_now) {
                // not planning to evict this item from memory within 'ages_flushing_now' ages
                continue;
            }

            // Skip entries with ref_count != 1 early
            // In 99% of cases, these will be rejected by try_make_entry_for_flush or evict_from_cache anyway
            // Filtering here avoids unnecessary work and reduces write lock contention in evict_from_cache
            if v.ref_count() != 1 {
                continue;
            }

            if v.dirty() {
                if collect_flush_candidates {
                    candidates_to_flush.push(*k);
                }
            } else {
                sampling_state.select(*k, &mut rng);
            }
        }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L29-49)
```rust
///
/// This number should be *at least* the worst case rate that entries are added to the in-mem
/// index per bin.  This ensures we start evicting early enough so that we do not exceed the
/// configured index threshold limit.
///
/// At the same time, we want this value to be as small as possible.  The smaller this value, the
/// higher the utilization of the in-mem index bins.
///
/// This value is used to compute the high watermark.
pub const DEFAULT_NUM_ENTRIES_OVERHEAD: usize = 5_000;

/// The number of entries to evict, once we've hit the high watermark.
///
/// We want this number to be small, similar to `NUM_ENTRIES_OVERHEAD`, to keep utilization high.
/// It also must be large enough to ensure once an eviction is triggered that scanning + flushing +
/// evicting completes before the high watermark is crossed again.
/// We also want to avoid/ammortize scanning the bins for flush/evict, so a larger number helps
/// with that goal.
///
/// This value is used to compute the low watermark.
pub const DEFAULT_NUM_ENTRIES_TO_EVICT: usize = 10_000;
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L536-546)
```rust
/// Precomputed thresholds derived from the configured per-bin target.
#[derive(Clone, Copy, Debug)]
pub struct ThresholdEntriesPerBin {
    /// Rounded target entries per bin used as the baseline for thresholds.
    pub target_entries: usize,
    /// Entry count above which a bin triggers flushing to disk and eviction
    /// from in-memory index.
    pub high_water_mark: usize,
    /// Entry count to reach after flushing/evicting from a bin.
    pub low_water_mark: usize,
}
```
