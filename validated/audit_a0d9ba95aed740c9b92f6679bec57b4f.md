### Title
`is_candidate_for_shrink` silently ignores the configured `shrink_ratio` in `TotalSpace` mode, making shrink/ancient-pack candidacy decisions diverge from operator-configured thresholds - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::is_candidate_for_shrink` is the single-store gate used both by ad-hoc shrink candidate discovery and by ancient append-vec packing (`calc_ancient_slot_info`) to decide whether an individual storage should be considered for shrinking. When the configured mode is `AccountShrinkThreshold::TotalSpace { shrink_ratio }`, the function explicitly discards the ratio value (`shrink_ratio: _`) and instead applies a hard-coded criterion (`alive_bytes < total_bytes`), rather than ever comparing against the operator-configured `--accounts-shrink-ratio` value. [1](#0-0) 

### Finding Description
This is structurally the same bug class as the reported H-5 issue: a value meant to shape a threshold calculation (there, `oracleSlippagePercent`; here, `shrink_ratio` for `TotalSpace` mode) is computed/known but never actually applied to the comparison, so the effective threshold silently diverges from what governance/operators configured.

`is_candidate_for_shrink` computes `total_bytes` and `alive_bytes`, then branches on `self.shrink_ratio`:
```
match self.shrink_ratio {
    AccountShrinkThreshold::TotalSpace { shrink_ratio: _ } => alive_bytes < total_bytes,
    AccountShrinkThreshold::IndividualStore { shrink_ratio } => {
        (alive_bytes as f64 / total_bytes as f64) < shrink_ratio
    }
}
``` [2](#0-1) 

In `IndividualStore` mode the ratio is correctly used as a per-store threshold. In `TotalSpace` mode, however, the pattern `shrink_ratio: _` explicitly binds and discards the configured ratio, replacing the intended "per-store fraction of dead bytes" check with a fixed `alive_bytes < total_bytes` (equivalent to a ratio of 1.0/"any dead bytes at all"). This function is the exact analog of the vault's `params.minPrimary` being overwritten by a fixed factor while the caller-supplied/second factor (`oracleSlippagePercent`) is dropped on the floor.

This gate is load-bearing in two places:
1. `calc_ancient_slot_info` calls `self.is_candidate_for_shrink(&storage)` per-slot to decide `should_shrink` for ancient-append-vec packing, directly driving `AncientSlotInfos::add`'s `should_shrink` classification. [3](#0-2) 
2. Test code demonstrates the intended semantics diverge between `TotalSpace` (ratio ignored, fixed 1.0 threshold) and `IndividualStore` (ratio honored) for the exact same storage state. [4](#0-3) 

By contrast, the *batch-level* selection helper `select_candidates_by_total_usage`, which is also used for `TotalSpace`, does correctly consume and apply the passed-in `shrink_ratio` against the running total alive/total byte ratio. [5](#0-4) 

So there are two different, inconsistent notions of "candidate" active simultaneously under `TotalSpace` mode: the batch-level check (`select_candidates_by_total_usage`) that actually respects the configured ratio, and the per-store check (`is_candidate_for_shrink`) that ignores it entirely and instead always requires only `alive_bytes < total_bytes`. Any code path relying on `is_candidate_for_shrink` (notably ancient-storage packing) will not honor the operator's `--accounts-shrink-ratio` setting at all when `--accounts-shrink-optimize-total-space` (i.e., `TotalSpace`) is selected — which is the default configuration path built by `validator/src/commands/run/execute.rs`. [6](#0-5) 

### Impact Explanation
The configured `shrink_ratio` for `TotalSpace` mode has no effect on `is_candidate_for_shrink`, meaning ancient append-vec packing decisions (`calc_ancient_slot_info` → `AncientSlotInfos::add` → `should_shrink`) are always driven by a fixed, more-aggressive-than-intended threshold (any dead bytes at all) regardless of what the operator configured. This is a disproportionate storage/CPU-cost class of issue: nodes configured to tolerate a looser shrink ratio (e.g., to reduce I/O/CPU overhead from excessive repacking) will still have every storage with even a single dead byte treated as an ancient-shrink candidate, causing more frequent/aggressive ancient repacking than intended, unnecessarily consuming I/O and CPU. It does not cause consensus-breaking hash divergence, but it is a real logic bug where a governance/operator-configured tuning knob is silently inert for a specific, reachable code path (`TotalSpace` + ancient packing), producing behavior inconsistent with the documented/expected configuration semantics.

### Likelihood Explanation
`TotalSpace` is the default shrink-threshold variant built by the validator CLI parsing (`accounts_shrink_optimize_total_space` defaults are wired through `execute.rs`), and ancient append-vec combination runs routinely as part of background account maintenance (`shrink_ancient_slots` → `calc_ancient_slot_info`). This means the discrepancy is triggered on essentially every validator running with default/`TotalSpace` configuration, not on some corner case — the code path is exercised on every ancient-slot maintenance pass.

### Recommendation
In `is_candidate_for_shrink`, apply `shrink_ratio` consistently for `TotalSpace` mode as well, mirroring the semantics used by `select_candidates_by_total_usage` (or, at minimum, use the per-store alive fraction against the configured ratio rather than a hardcoded `alive_bytes < total_bytes`):
```rust
match self.shrink_ratio {
    AccountShrinkThreshold::TotalSpace { shrink_ratio } => {
        (alive_bytes as f64 / total_bytes as f64) < shrink_ratio
    }
    AccountShrinkThreshold::IndividualStore { shrink_ratio } => {
        (alive_bytes as f64 / total_bytes as f64) < shrink_ratio
    }
}
```
Add/adjust unit tests analogous to `test_is_candidate_for_shrink` to assert that changing `shrink_ratio` under `TotalSpace` mode changes the candidacy result, matching `IndividualStore` behavior, and add ancient-packing tests that vary `shrink_ratio` under `TotalSpace` to confirm `should_shrink` respects the configured value.

### Proof of Concept
1. Configure the validator (or a unit test) with `AccountShrinkThreshold::TotalSpace { shrink_ratio: 0.5 }` (i.e., intend to only shrink stores that are less than 50% alive).
2. Create a storage with `alive_bytes = 0.9 * total_bytes` (90% alive, well above the configured 50% ratio threshold, and thus should NOT be a shrink candidate).
3. Call `accounts_db.is_candidate_for_shrink(&store)` — it returns `true` because `alive_bytes < total_bytes` is satisfied, even though `alive_bytes / total_bytes = 0.9 > 0.5` should have excluded it. This mirrors `test_is_candidate_for_shrink`'s pattern where the `IndividualStore` branch is validated against a `shrink_ratio` of 0.3 and returns different candidacy than an equivalent `TotalSpace` configuration would, showing the `TotalSpace` code path never consults its own `shrink_ratio`. [7](#0-6) 
4. Feed the same storage set through `calc_ancient_slot_info`, and observe `should_shrink` is set to `true` for the 90%-alive storage purely because `is_candidate_for_shrink` used the fixed criterion, causing unnecessary ancient repacking work that the operator's 0.5 ratio was meant to prevent. [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_db.rs (L3037-3061)
```rust
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
```

**File:** accounts-db/src/accounts_db.rs (L5045-5056)
```rust
    /// Determines whether a given AccountStorageEntry instance is a
    /// candidate for shrinking.
    pub(crate) fn is_candidate_for_shrink(&self, store: &AccountStorageEntry) -> bool {
        let total_bytes = store.written_bytes();
        let alive_bytes = self.alive_bytes_after_shrink(store) as u64;
        match self.shrink_ratio {
            AccountShrinkThreshold::TotalSpace { shrink_ratio: _ } => alive_bytes < total_bytes,
            AccountShrinkThreshold::IndividualStore { shrink_ratio } => {
                (alive_bytes as f64 / total_bytes as f64) < shrink_ratio
            }
        }
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L598-613)
```rust
        for slot in &slots {
            if let Some(storage) = self.storage.get_slot_storage_entry(*slot) {
                let is_candidate_for_shrink = self.is_candidate_for_shrink(&storage);
                let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&storage) as u64;
                if infos.add(
                    *slot,
                    storage,
                    alive_bytes_after_shrink,
                    tuning.can_randomly_shrink,
                    tuning.ideal_storage_size,
                    is_high_slot(*slot),
                    is_candidate_for_shrink,
                ) {
                    randoms += 1;
                }
            }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L5355-5384)
```rust
    match accounts.shrink_ratio {
        AccountShrinkThreshold::TotalSpace { shrink_ratio } => {
            assert_eq!(
                (DEFAULT_ACCOUNTS_SHRINK_RATIO * 100.) as u64,
                (shrink_ratio * 100.) as u64
            )
        }
        AccountShrinkThreshold::IndividualStore { shrink_ratio: _ } => {
            panic!("Expect the default to be TotalSpace")
        }
    }

    entry
        .num_alive_bytes
        .store(written_bytes - 1, Ordering::Release);
    assert!(accounts.is_candidate_for_shrink(&entry));
    entry
        .num_alive_bytes
        .store(written_bytes, Ordering::Release);
    assert!(!accounts.is_candidate_for_shrink(&entry));

    let shrink_ratio = 0.3;
    let file_size_shrink_limit = (written_bytes as f64 * shrink_ratio) as usize;
    entry
        .num_alive_bytes
        .store(file_size_shrink_limit + 1, Ordering::Release);
    accounts.shrink_ratio = AccountShrinkThreshold::TotalSpace { shrink_ratio };
    assert!(accounts.is_candidate_for_shrink(&entry));
    accounts.shrink_ratio = AccountShrinkThreshold::IndividualStore { shrink_ratio };
    assert!(!accounts.is_candidate_for_shrink(&entry));
```

**File:** validator/src/commands/run/execute.rs (L536-540)
```rust
    let shrink_ratio = if accounts_shrink_optimize_total_space {
        AccountShrinkThreshold::TotalSpace { shrink_ratio }
    } else {
        AccountShrinkThreshold::IndividualStore { shrink_ratio }
    };
```
