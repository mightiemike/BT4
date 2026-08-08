### Title
Fixed `CLEAN_INTERVAL` background cleanup cadence ignores actual configured snapshot interval, risking disproportionate accounts storage/CPU growth - (File: runtime/src/accounts_background_service.rs)

### Summary
`AccountsBackgroundService`'s main loop decides when to run `clean_accounts`/flush using a hardcoded wall-clock constant, `CLEAN_INTERVAL = Duration::from_secs(50)`, that is explicitly calibrated to the *default* incremental snapshot interval (100 slots), not the operator-configured one. [1](#0-0) 
This mirrors the Renzo bug class: a single fixed "staleness/heartbeat" constant is assumed valid across all configurations/chains, when in reality the parameter it substitutes for (snapshot cadence) is configurable and can diverge widely from the assumption.

### Finding Description
The comment directly states the intent: "Set the clean interval duration to be approximately how long before the next incremental snapshot request is received, plus some buffer. The default incremental snapshot interval is 100 slots, which ends up being 40 seconds plus buffer." [1](#0-0) 

This constant is only used relative to wall-clock elapsed time, not to the actual configured `incremental_snapshot_archive_interval`: [2](#0-1) 

However, `--snapshot-interval-slots` is validator-configurable to any non-zero value with no minimum tied to `CLEAN_INTERVAL`, and no cross-check that it corresponds with the hardcoded 50-second assumption: [3](#0-2) [4](#0-3) 

When a snapshot request is handled, `previous_clean_time`/`previous_shrink_time` are reset regardless of whether `CLEAN_INTERVAL` had already elapsed: [5](#0-4) 
Since only one branch (snapshot handling vs. periodic clean/shrink) executes per loop iteration, if snapshot requests are configured/received much more frequently than every ~50 seconds (e.g., a much smaller `--snapshot-interval-slots`, or shorter/faster slot times than the ~400ms default used to derive "40 seconds"), the periodic clean/shrink branch is effectively starved: clean only happens as a side effect of snapshot handling, and the standalone `should_clean`/`should_shrink` timers never fire because they keep getting reset. Conversely, if snapshot requests come much less frequently than assumed (interval configured larger, or slot times slower), `CLEAN_INTERVAL`'s fixed 50s assumption no longer matches "approximately when the next snapshot request arrives," and clean/flush cadence becomes decoupled from the actual accumulation rate of dirty/uncleaned account state, exactly like the Renzo bug where the fixed 24h+60s heartoken assumption stopped matching per-chain/per-token actual update cadence.

### Impact Explanation
This can lead to disproportionate storage/CPU cost on an honest node: `dirty_stores`, `uncleaned_pubkeys`, and cached/unflushed slots can accumulate beyond what the fixed interval was designed to bound, because the interval is a static wall-clock guess rather than being derived from (or validated against) the actual configured snapshot cadence. This falls squarely in the accepted impact category of "disproportionate storage and CPU cost," and is reachable purely through standard validator configuration (`--snapshot-interval-slots`), not privileged/mocked/theoretical access.

### Likelihood Explanation
Likelihood is moderate: it requires an operator to run with a `--snapshot-interval-slots` value that diverges significantly from the default (100 slots), which is an explicitly supported and documented configuration knob, not a misconfiguration. No malicious peer input is needed — it is purely a function of local validator configuration and slot cadence, keeping it in the class of a self-inflicted but exploitable-by-configuration operational bug rather than validator/peer/operator role abuse.

### Recommendation
Derive `CLEAN_INTERVAL` (and `SHRINK_INTERVAL` if similarly related) dynamically from the actual configured `incremental_snapshot_archive_interval` (in slots, converted using the bank's current `ns_per_slot`) instead of a fixed 50-second constant, similar to how `rpc/src/rpc_service.rs` computes `snapshot_timeout` from the configured interval and per-slot timing rather than a fixed constant. [6](#0-5) 

### Proof of Concept
1. Start a validator with `--snapshot-interval-slots` set far below the default (e.g., 5 instead of 100), so that snapshot requests arrive roughly every few seconds instead of ~40 seconds.
2. Observe that in `AccountsBackgroundService`'s loop, `handle_snapshot_requests` returns `Some(...)` almost every iteration, continually resetting `previous_clean_time`/`previous_shrink_time` before the fixed `CLEAN_INTERVAL`/`SHRINK_INTERVAL` timers can elapse. [7](#0-6) 
3. Because the periodic clean/shrink branch (`should_clean`/`should_shrink`) is starved by the reset, `dirty_stores`/`uncleaned_pubkeys` and unshrunk storages accumulate at a rate proportional to the actual (fast) snapshot cadence rather than the assumed 40s+buffer cadence baked into `CLEAN_INTERVAL`, producing disproportionate memory/CPU growth relative to what the hardcoded constant was designed to bound.

### Citations

**File:** runtime/src/accounts_background_service.rs (L43-47)
```rust
// Set the clean interval duration to be approximately how long before the next incremental
// snapshot request is received, plus some buffer.  The default incremental snapshot interval is
// 100 slots, which ends up being 40 seconds plus buffer.
const CLEAN_INTERVAL: Duration = Duration::from_secs(50);
const SHRINK_INTERVAL: Duration = Duration::from_secs(1);
```

**File:** runtime/src/accounts_background_service.rs (L492-513)
```rust
                        let snapshot_handle_result =
                            request_handlers.handle_snapshot_requests(non_snapshot_time);

                        if let Some(snapshot_handle_result) = snapshot_handle_result {
                            // Safe, see proof above

                            last_snapshot_end_time = Some(Instant::now());
                            match snapshot_handle_result {
                                Ok(snapshot_slot) => {
                                    assert!(
                                        last_cleaned_slot <= snapshot_slot,
                                        "last cleaned slot: {last_cleaned_slot}, snapshot request \
                                         slot: {snapshot_slot}, enqueued snapshot requests: {:?}",
                                        request_handlers
                                            .snapshot_request_handler
                                            .snapshot_request_receiver
                                            .try_iter()
                                            .collect::<Vec<_>>(),
                                    );
                                    last_cleaned_slot = snapshot_slot;
                                    previous_clean_time = Instant::now();
                                    previous_shrink_time = Instant::now();
```

**File:** runtime/src/accounts_background_service.rs (L540-557)
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
```

**File:** validator/src/commands/run/args.rs (L399-413)
```rust
    .arg(
        Arg::with_name("snapshot_interval_slots")
            .long("snapshot-interval-slots")
            .alias("incremental-snapshot-interval-slots")
            .value_name("NUMBER")
            .takes_value(true)
            .default_value(&default_args.incremental_snapshot_archive_interval_slots)
            .validator(is_non_zero)
            .help("Number of slots between generating snapshots")
            .long_help(
                "Number of slots between generating snapshots. If incremental snapshots are \
                 enabled, this sets the incremental snapshot interval. If incremental snapshots \
                 are disabled, this sets the full snapshot interval. Must be greater than zero.",
            ),
    )
```

**File:** validator/src/commands/run/execute.rs (L1199-1218)
```rust
) -> Result<SnapshotConfig, Box<dyn std::error::Error>> {
    let (full_snapshot_archive_interval, incremental_snapshot_archive_interval) =
        if matches.is_present("no_snapshots") {
            // snapshots are disabled
            (SnapshotInterval::Disabled, SnapshotInterval::Disabled)
        } else {
            match (
                incremental_snapshot_fetch,
                value_t_or_exit!(matches, "snapshot_interval_slots", NonZeroU64),
            ) {
                (true, incremental_snapshot_interval_slots) => {
                    // incremental snapshots are enabled
                    // use --snapshot-interval-slots for the incremental snapshot interval
                    let full_snapshot_interval_slots =
                        value_t_or_exit!(matches, "full_snapshot_interval_slots", NonZeroU64);
                    (
                        SnapshotInterval::Slots(full_snapshot_interval_slots),
                        SnapshotInterval::Slots(incremental_snapshot_interval_slots),
                    )
                }
```

**File:** rpc/src/rpc_service.rs (L282-308)
```rust
        let snapshot_timeout = self.snapshot_config.as_ref().and_then(|config| {
            snapshot_type.map(|st| {
                let interval = match st {
                    SnapshotKind::Full => config.full_snapshot_archive_interval,
                    SnapshotKind::Incremental => config.incremental_snapshot_archive_interval,
                };
                let computed = match interval {
                    SnapshotInterval::Disabled => Duration::ZERO,
                    SnapshotInterval::Slots(slots) => {
                        let ns_per_slot = self
                            .bank_forks
                            .read()
                            .unwrap()
                            .root_bank()
                            .ns_per_slot
                            .try_into()
                            .unwrap_or(solana_clock::DEFAULT_MS_PER_SLOT * 1_000_000);
                        Duration::from_nanos(slots.get().saturating_mul(ns_per_slot))
                    }
                };
                let fallback = match st {
                    SnapshotKind::Full => FALLBACK_FULL_SNAPSHOT_TIMEOUT_SECS,
                    SnapshotKind::Incremental => FALLBACK_INCREMENTAL_SNAPSHOT_TIMEOUT_SECS,
                };
                std::cmp::max(computed, fallback)
            })
        });
```
