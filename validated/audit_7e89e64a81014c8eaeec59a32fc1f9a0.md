### Title
Aggregate-ratio shrink gating can indefinitely defer reclamation of individually sparse AppendVec stores under `AccountShrinkThreshold::TotalSpace` - (File: accounts-db/src/accounts_db.rs)

### Summary
`AccountsDb::select_candidates_by_total_usage()` (the default `TotalSpace` shrink strategy) decides whether to shrink a sparse storage by checking a *system-wide aggregate* alive-byte ratio rather than the individual store's own alive ratio. As with the DYAD `VaultManagerV2::liquidate()` bug — where an aggregate collateral ratio masked an individually under-collateralized vault and blocked liquidation — this code can let individual, heavily-dead AppendVec stores persist unshrunk as long as the *pool-wide* ratio still looks healthy, because dense candidates in the same batch subsidize the aggregate metric.

### Finding Description
`select_candidates_by_total_usage` [1](#0-0)  sorts shrink candidates ascending by their own `alive_ratio`, then walks the sorted list computing a *running aggregate* `alive_ratio = total_alive_bytes / total_bytes` over the whole remaining candidate set:

```
if alive_ratio > shrink_ratio {
    // we have reached our goal, stop
    if store_usage.alive_ratio < shrink_ratio {
        shrink_slots_next_batch.insert(store_usage.slot);
    } else {
        break;
    }
} else {
    ...
    shrink_slots.insert(store_usage.slot, Arc::clone(store));
}
``` [2](#0-1) 

Because the loop evaluates the **aggregate** ratio before deciding whether to shrink the **individual** sparsest store, a store whose own `alive_ratio` is far below `shrink_ratio` (e.g., 5% alive) is not shrunk in this pass if the pool of candidates it shares the batch with is otherwise dense enough to keep the aggregate above the threshold — exactly the pattern in the reported DYAD issue, where a vault's individual (exogenous) collateral ratio fell under 100% but the aggregate (exo+kerosene) ratio stayed above 150%, blocking liquidation.

The default configuration uses this `TotalSpace` strategy [3](#0-2) , in contrast to `AccountShrinkThreshold::IndividualStore`, which would shrink strictly based on each store's own ratio [4](#0-3)  and `accounts-db/src/accounts_db.rs:5045-5056`.

Deferred candidates are re-inserted into `shrink_candidate_slots` for a future round [5](#0-4) , so in principle this is only a delay, not permanent starvation. However, because the aggregate check is re-evaluated fresh from the top of the (again ascending-sorted) candidate list every round, a sparse store can be re-deferred repeatedly for as long as the candidate pool continues to contain enough dense members to keep the pool-wide ratio above `shrink_ratio` — there is no per-store aging/priority mechanism forcing eventual reclamation independent of the aggregate metric.

### Impact Explanation
This falls under the accepted "disproportionate storage and CPU cost" impact category. A store that is mostly dead space (e.g., a validator's AppendVec holding many closed/zeroed accounts) can remain on disk far longer than an `IndividualStore`-style policy would allow, because the aggregate metric can mask its individually poor utilization. This inflates disk usage and increases the number of storages that must be scanned in subsequent `clean_accounts`/index-generation/snapshot passes, without any correctness (hash/capitalization) divergence — this is a resource-bloat issue, not silent balance/hash corruption.

### Likelihood Explanation
This is deterministic, standard-code-path behavior under the *default* `AccountShrinkThreshold::TotalSpace` configuration [3](#0-2) ; no malicious input or crafted snapshot is required — it emerges naturally whenever the shrink-candidate pool mixes a few very sparse stores with many dense ones, which is a normal, expected state during validator operation (e.g., after selective account closures). Whether this materializes into meaningfully "disproportionate" cost in practice depends on real-world candidate-pool composition and shrink cadence, which I was not able to fully quantify from static code inspection alone — this is an architectural characteristic of the aggregate-based algorithm rather than a confirmed measured regression, and the existing `shrink_slots_next_batch` re-queue mechanism partially mitigates full starvation.

### Recommendation
Consider adding a per-store deferral counter/age (similar to the ancient-storage tuning in `ancient_append_vecs.rs`) so that a store whose own `alive_ratio` remains persistently far below `shrink_ratio` after N deferred rounds is forcibly shrunk regardless of the pool-wide aggregate ratio, bounding worst-case storage bloat the same way the DYAD recommendation proposed gating liquidation on the individual (exogenous) ratio rather than solely the aggregate.

### Proof of Concept
Not independently verified with a runnable reproduction; based on static analysis of `select_candidates_by_total_usage` and its test coverage. The existing unit tests (`test_select_candidates_by_total_usage_3_way_split_condition`, `accounts-db/src/accounts_db/tests/impl.rs:2921-2991`) already demonstrate the mechanism: a store with `alive_ratio` of 0.5 (below the 0.6 test threshold) is deferred to `next_candidates` purely because the aggregate ratio of the batch (0.75) exceeds the threshold, confirming that individually-eligible sparse stores are skipped whenever the aggregate metric looks healthy [6](#0-5) . A full end-to-end PoC demonstrating sustained multi-round deferral leading to measurable disk bloat would require a running validator/test harness beyond what static code review can confirm here.

### Citations

**File:** accounts-db/src/accounts_db.rs (L360-370)
```rust
#[derive(Debug, Clone, Copy)]
pub enum AccountShrinkThreshold {
    /// Measure the total space sparseness across all candidates
    /// And select the candidates by using the top sparse account storage entries to shrink.
    /// The value is the overall shrink threshold measured as ratio of the total live bytes
    /// over the total bytes.
    TotalSpace { shrink_ratio: f64 },
    /// Use the following option to shrink all stores whose alive ratio is below
    /// the specified threshold.
    IndividualStore { shrink_ratio: f64 },
}
```

**File:** accounts-db/src/accounts_db.rs (L371-377)
```rust
pub const DEFAULT_ACCOUNTS_SHRINK_OPTIMIZE_TOTAL_SPACE: bool = true;
pub const DEFAULT_ACCOUNTS_SHRINK_RATIO: f64 = 0.80;
// The default extra account space in percentage from the ideal target
const DEFAULT_ACCOUNTS_SHRINK_THRESHOLD_OPTION: AccountShrinkThreshold =
    AccountShrinkThreshold::TotalSpace {
        shrink_ratio: DEFAULT_ACCOUNTS_SHRINK_RATIO,
    };
```

**File:** accounts-db/src/accounts_db.rs (L2985-3071)
```rust
    /// Given the input `ShrinkCandidates`, this function sorts the stores by their alive ratio
    /// in increasing order with the most sparse entries in the front. It will then simulate the
    /// shrinking by working on the most sparse entries first and if the overall alive ratio is
    /// achieved, it will stop and return:
    /// first tuple element: the filtered-down candidates and
    /// second duple element: the candidates which
    /// are skipped in this round and might be eligible for the future shrink.
    fn select_candidates_by_total_usage(
        &self,
        shrink_slots: &ShrinkCandidates,
        shrink_ratio: f64,
    ) -> (IntMap<Slot, Arc<AccountStorageEntry>>, ShrinkCandidates) {
        struct StoreUsageInfo {
            slot: Slot,
            alive_ratio: f64,
            alive_bytes_after_shrink: u64,
            store: Arc<AccountStorageEntry>,
        }
        let mut store_usages = Vec::with_capacity(shrink_slots.len());
        let mut total_alive_bytes: u64 = 0;
        let mut total_bytes: u64 = 0;
        for slot in shrink_slots {
            let Some(store) = self.storage.get_slot_storage_entry(*slot) else {
                continue;
            };
            let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&store) as u64;
            total_alive_bytes += alive_bytes_after_shrink;
            let written_bytes = store.written_bytes();
            total_bytes += written_bytes;
            debug_assert!(
                written_bytes > 0,
                "shrink candidate has zero written bytes! slot: {slot} id: {}",
                store.id(),
            );
            let alive_ratio = alive_bytes_after_shrink as f64 / written_bytes as f64;
            store_usages.push(StoreUsageInfo {
                slot: *slot,
                alive_ratio,
                alive_bytes_after_shrink,
                store: store.clone(),
            });
        }
        store_usages.sort_by(|a, b| {
            a.alive_ratio
                .partial_cmp(&b.alive_ratio)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        // Working from the beginning of store_usage which are the most sparse and see when we can stop
        // shrinking while still achieving the overall goals.
        let mut shrink_slots = IntMap::default();
        let mut shrink_slots_next_batch = ShrinkCandidates::default();
        for store_usage in &store_usages {
            let store = &store_usage.store;
            let alive_ratio = (total_alive_bytes as f64) / (total_bytes as f64);
            debug!(
                "alive_ratio: {:?} store_id: {:?}, store_ratio: {:?} requirement: {:?}, \
                 total_bytes: {:?} total_alive_bytes: {:?}",
                alive_ratio,
                store_usage.store.id(),
                store_usage.alive_ratio,
                shrink_ratio,
                total_bytes,
                total_alive_bytes
            );
            if alive_ratio > shrink_ratio {
                // we have reached our goal, stop
                debug!(
                    "Shrinking goal can be achieved at slot {:?}, total_alive_bytes: {:?} \
                     total_bytes: {:?}, alive_ratio: {:}, shrink_ratio: {:?}",
                    store_usage.slot, total_alive_bytes, total_bytes, alive_ratio, shrink_ratio
                );
                if store_usage.alive_ratio < shrink_ratio {
                    shrink_slots_next_batch.insert(store_usage.slot);
                } else {
                    break;
                }
            } else {
                let current_store_size = store.written_bytes();
                let after_shrink_size = store_usage.alive_bytes_after_shrink;
                let bytes_saved = current_store_size.saturating_sub(after_shrink_size);
                total_bytes -= bytes_saved;
                shrink_slots.insert(store_usage.slot, Arc::clone(store));
            }
        }
        (shrink_slots, shrink_slots_next_batch)
    }
```

**File:** accounts-db/src/accounts_db.rs (L3185-3192)
```rust
        let mut pended_counts: usize = 0;
        if let Some(shrink_slots_next_batch) = shrink_slots_next_batch {
            let mut shrink_slots = self.shrink_candidate_slots.lock().unwrap();
            pended_counts = shrink_slots_next_batch.len();
            for slot in shrink_slots_next_batch {
                shrink_slots.insert(slot);
            }
        }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L2980-2991)
```rust
    // Set the target alive ratio to 0.6 so that we can just get rid of store1, the remaining two stores
    // alive ratio can be > the target ratio: the actual ratio is 0.75 because of 150 alive bytes / 200 total bytes.
    // The target ratio is also set to larger than store2's alive ratio: 0.5 so that it would be added
    // to the candidates list for next round.
    let target_alive_ratio = 0.6;
    let (selected_candidates, next_candidates) =
        db.select_candidates_by_total_usage(&candidates, target_alive_ratio);
    assert_eq!(1, selected_candidates.len());
    assert!(selected_candidates.contains(&store1_slot));
    assert_eq!(1, next_candidates.len());
    assert!(next_candidates.contains(&store2_slot));
}
```
