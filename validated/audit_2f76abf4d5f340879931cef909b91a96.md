### Title
`CLEAN_INTERVAL`/`SHRINK_INTERVAL` in AccountsBackgroundService are hardcoded to the legacy 400ms slot-time baseline and do not scale with the reduced-slot-time feature - ([File: runtime/src/accounts_background_service.rs])

### Summary
`AccountsBackgroundService` gates the accounts-DB `clean_accounts`/`shrink_candidate_slots`/`shrink_ancient_slots` maintenance cycle behind fixed wall-clock timers (`CLEAN_INTERVAL = 50s`, `SHRINK_INTERVAL = 1s`) that were explicitly derived from the legacy 400ms-per-slot assumption. The codebase has since introduced a `slot_params` mechanism that can reduce the effective slot time (200/250/300/350ms) via feature activation, but these two timers were never made a function of the active slot-time regime, exactly the same bug class as the Moonwell `borrowRateMaxMantissa` finding: a per-block-time-derived constant hardcoded for one timing assumption and never adjusted when the "chain" (here, the network's effective slot cadence) changes.

### Finding Description
`CLEAN_INTERVAL` and `SHRINK_INTERVAL` are declared as compile-time constants: [1](#0-0) 

The comment on `CLEAN_INTERVAL` makes the block-time assumption explicit: "the default incremental snapshot interval is 100 slots, which ends up being 40 seconds plus buffer" — i.e., the 50-second value is derived from `100 slots * 400ms/slot`. This value is used directly as a wall-clock `Instant::elapsed()` comparison to decide when to run `clean_accounts`/`shrink_candidate_slots`/`shrink_ancient_slots`: [2](#0-1) 

Separately, the runtime now supports reducing the effective slot time via feature activation, changing `ns_per_slot` from the legacy 400,000,000 ns down to as low as 200,000,000 ns, with dedicated `SlotParams` tables for each regime: [3](#0-2) 

Nowhere in `AccountsBackgroundService::new` (nor anywhere else in the repo — confirmed by searching for `CLEAN_INTERVAL`/`SHRINK_INTERVAL` usages, which only appear in this one file) is `CLEAN_INTERVAL`/`SHRINK_INTERVAL` derived from `bank.ns_per_slot`, `slot_params`, or any per-epoch/regime-aware value. The 100-incremental-snapshot-slot assumption baked into the constant's comment only holds at the legacy 400ms slot time; as soon as a slot-time-reduction feature is active, the actual wall-clock interval between incremental snapshot requests shrinks proportionally (e.g., to 20 seconds at 200ms slots), while `CLEAN_INTERVAL` stays fixed at 50 seconds.

### Impact Explanation
Because `clean_accounts`/`shrink_candidate_slots` are throttled by a stale wall-clock constant that no longer matches the actual slot cadence once slot time is reduced, many more slots' worth of dirty/uncleaned account data and shrink candidates accumulate per maintenance cycle than the constant was designed for. This causes AccountsDb `dirty_stores`/`uncleaned_pubkeys`/shrink-candidate backlogs to grow substantially larger between clean/shrink passes than the interval was tuned for, leading to disproportionate memory, storage, and CPU cost when clean/shrink finally runs (larger batches to process at once), and correspondingly larger on-disk stale append-vec footprint in between passes. This is a resource/liveness degradation of core AccountsDb maintenance rather than a consensus-breaking bug, but it directly reproduces the reported bug class: a hardcoded, block-time-derived threshold that silently becomes wrong (too loose) when the underlying block/slot cadence changes.

### Likelihood Explanation
This is triggered automatically and deterministically whenever a slot-time-reduction feature (e.g. `reduce_slot_time_to_200ms`) becomes active on the cluster — no attacker action or malicious input is required, only normal feature-gate activation that the codebase already supports and tests for (`slot_time_feature_gates`, `SLOT_PARAMS_200MS`, etc.). Every validator running the affected code experiences the same stale-timer behavior identically, so this is a systemic node-level operational issue rather than a rare edge case.

### Recommendation
Derive `CLEAN_INTERVAL` (and ideally `SHRINK_INTERVAL`) from the bank's currently effective slot-time parameters (e.g., `bank.slot_params()`/`ns_per_slot()` and the configured incremental snapshot slot interval) rather than a compile-time constant tied to the legacy 400ms baseline, so the maintenance cadence scales automatically as slot time changes.

### Proof of Concept
1. Activate `reduce_slot_time_to_200ms` (or any of the other slot-time-reduction feature gates in `slot_params.rs`) on a test cluster/bank.
2. Observe that `bank.ns_per_slot`/`slots_per_year` change to the reduced-slot-time regime (as validated in `test_reduce_slot_time_features_active_at_genesis` in `runtime/src/bank/tests.rs`), while incremental snapshot requests now arrive roughly twice as frequently in wall-clock time.
3. Confirm `CLEAN_INTERVAL`/`SHRINK_INTERVAL` in `runtime/src/accounts_background_service.rs` remain unchanged (`50s`/`1s`), so `clean_accounts`/`shrink_candidate_slots` still only run on the old cadence, causing accumulation of roughly 2x the intended number of dirty/uncleaned slots per maintenance pass relative to what the constants were tuned for.

### Citations

**File:** runtime/src/accounts_background_service.rs (L37-47)
```rust
/// Limit the maximum frequency that the ABS main loop can run.
/// If the loop ran for less than this duration, sleep the remainder.
/// E.g. with a min interval of 100 millis, the loop will run a maximum
/// of 10 times per second.  Lower frequency is allowed, and occurs
/// when longer-running tasks are triggered.
const MIN_LOOP_INTERVAL: Duration = Duration::from_millis(100);
// Set the clean interval duration to be approximately how long before the next incremental
// snapshot request is received, plus some buffer.  The default incremental snapshot interval is
// 100 slots, which ends up being 40 seconds plus buffer.
const CLEAN_INTERVAL: Duration = Duration::from_secs(50);
const SHRINK_INTERVAL: Duration = Duration::from_secs(1);
```

**File:** runtime/src/accounts_background_service.rs (L540-571)
```rust
                            let duration_since_previous_clean = previous_clean_time.elapsed();
                            let should_clean = duration_since_previous_clean > CLEAN_INTERVAL;

                            // if we're cleaning, then force flush, otherwise be lazy
                            let force_flush = should_clean;
                            bank.rc
                                .accounts
                                .accounts_db
                                .flush_accounts_cache(force_flush, Some(max_clean_slot_inclusive));

                            if should_clean {
                                bank.rc
                                    .accounts
                                    .accounts_db
                                    .clean_accounts(Some(max_clean_slot_inclusive), false);
                                last_cleaned_slot = max_clean_slot_inclusive;
                                previous_clean_time = Instant::now();
                            }

                            let duration_since_previous_shrink = previous_shrink_time.elapsed();
                            let should_shrink = duration_since_previous_shrink > SHRINK_INTERVAL;
                            // To avoid pathological interactions between the clean and shrink
                            // timers, call shrink for either should_shrink or should_clean.
                            if should_shrink || should_clean {
                                if should_clean {
                                    // We used to only squash (aka shrink ancients) when we also
                                    // cleaned, so keep that same behavior here for now.
                                    bank.shrink_ancient_slots();
                                }
                                bank.shrink_candidate_slots();
                                previous_shrink_time = Instant::now();
                            }
```

**File:** runtime/src/slot_params.rs (L122-145)
```rust
pub const LEGACY_HASHES_PER_TICK: u64 = 62_500;
pub(crate) const LEGACY_SLOT_PARAMS: SlotParams = SlotParams {
    ns_per_slot: 400_000_000,
    slots_per_year: 78_892_314.984,
    hashes_per_tick: Some(LEGACY_HASHES_PER_TICK),
    cost_tracker_limits: CostTrackerLimits::new(24_000_000, 60_000_000, 100_000_000),
    max_data_shreds_per_slot: 32_768,
    max_code_shreds_per_slot: 32_768,
    max_entry_bytes_per_slot: 20 * 1024 * 1024,
    partitioned_epoch_rewards_stake_account_stores_per_block: 4096,
    vat_to_burn_per_epoch: 1_600_000_000,
};

pub(crate) const SLOT_PARAMS_350MS: SlotParams = SlotParams {
    ns_per_slot: 350_000_000,
    slots_per_year: 90_162_645.696,
    hashes_per_tick: Some(54_687),
    cost_tracker_limits: CostTrackerLimits::new(21_000_000, 52_500_000, 87_500_000),
    max_data_shreds_per_slot: 28_672,
    max_code_shreds_per_slot: 28_672,
    max_entry_bytes_per_slot: 18_350_080,
    partitioned_epoch_rewards_stake_account_stores_per_block: 3_584,
    vat_to_burn_per_epoch: 1_400_000_000,
};
```
