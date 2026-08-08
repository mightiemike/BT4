## Finding: Unbounded slot-range sweep held under a global mutex can produce disproportionate CPU/lock-contention cost — `sweep_slots_after_snapshot` (accounts-db/src/accounts_db.rs)

### Summary
The external report describes a loop that iterates from a "last processed" checkpoint to a "current" value without capping how far that range can grow, causing disproportionate cost when the gap becomes large. The closest reachable analog in the unprivileged AccountsDB clean/shrink path is `AccountsDb::sweep_slots_after_snapshot`, which iterates the entire slot range `[last_swept_full_snapshot_slot + 1, latest_full_snapshot_slot]` inline, while holding the `shrink_candidate_slots` mutex for the whole scan.

### Finding Description
`sweep_slots_after_snapshot` is invoked from `clean_accounts` whenever the "advanced since clean" flag is set and a full snapshot slot is known: [1](#0-0) 

The function itself walks every slot in the interval with no chunking, batching, or size cap, performing a storage lookup and productivity/candidacy checks per slot, all under a single held lock: [2](#0-1) 

The comment documents the assumption that this lock is safe because "the only paths that take this lock in production validator code run in earlier/later phases of the same AccountsBackgroundService iteration" — i.e., the safety argument is about correctness, not about bounding the size of the range or the duration the lock is held. Nothing in the function clamps `latest_full_snapshot_slot - last_swept_full_snapshot_slot`; the loop cost scales linearly with however large that gap has become.

Under normal steady-state operation, `clean_accounts` runs on a fixed interval (`CLEAN_INTERVAL`) from `AccountsBackgroundService`, so the gap between sweeps is small (bounded by how many slots elapse between clean invocations while a full snapshot slot advances): [3](#0-2) 

However, this is an accumulation invariant enforced only by the background service's scheduling cadence, not by the sweep function itself. If `last_swept_full_snapshot_slot` and `latest_full_snapshot_slot` diverge by a large amount — for example after loading a snapshot/restoring a bank where the previously recorded "last swept" checkpoint is much older than the currently loaded full-snapshot slot — the very next `clean_accounts` call will synchronously walk the entire accumulated slot range while holding `shrink_candidate_slots`, blocking any other producer/consumer of that lock (e.g., concurrent shrink candidate insertion or `shrink_candidate_slots()` invocations) for the full duration of the sweep.

This mirrors the reported bug class: a loop whose bound is a difference between a persisted "last processed" checkpoint and a "current" value, with no explicit cap on how large that difference can grow before the loop executes.

### Impact Explanation
If the gap between `last_swept_full_snapshot_slot` and `latest_full_snapshot_slot` becomes large (e.g., due to restart/restore scenarios where the checkpoint lags far behind), the sweep performs storage lookups and shrink-candidacy checks across the full slot range synchronously inside `clean_accounts`, while holding a mutex needed elsewhere in the background service. This causes a disproportionate CPU cost and lock-contention stall relative to normal operation, potentially delaying `clean_accounts`/shrink cycles and elevating memory/storage pressure until the sweep completes.

### Likelihood Explanation
In steady-state validator operation the gap is naturally small because `clean_accounts` runs frequently and the flag `latest_full_snapshot_slot_advanced_since_clean` triggers the sweep promptly after each full-snapshot slot advance. The scenario is most likely to manifest when the two checkpoints diverge outside of the normal per-clean cadence (e.g., across a snapshot load/restart boundary), which is a plausible, honest-node condition rather than a purely theoretical one, but it depends on how `last_swept_full_snapshot_slot` is (re)initialized relative to `latest_full_snapshot_slot` at startup — a detail I was not able to fully verify within the available search budget (only one reference was found in `ledger/src/bank_forks_utils.rs`, whose content I did not confirm).

### Recommendation
Bound the amount of work `sweep_slots_after_snapshot` performs per invocation — e.g., cap the number of slots processed per call and persist/advance `last_swept_full_snapshot_slot` incrementally across multiple `clean_accounts` cycles, or release/reacquire the `shrink_candidate_slots` lock periodically during the scan — so that a large accumulated gap cannot cause a single unbounded, lock-held iteration.

### Proof of Concept
Not independently reproduced; based on static analysis of the unbounded `for slot in start..=latest_full_snapshot_slot` loop in `sweep_slots_after_snapshot` [4](#0-3)  and its lock-holding pattern, combined with the invocation site in `clean_accounts` that only calls it under a single boolean flag with no range cap [5](#0-4) . I was unable to fully verify, within the tool budget available, whether the initialization/persistence logic in `ledger/src/bank_forks_utils.rs` can actually produce a large real-world gap between `last_swept_full_snapshot_slot` and `latest_full_snapshot_slot`; confirming that would require reading that file directly.

### Citations

**File:** accounts-db/src/accounts_db.rs (L1688-1712)
```rust
        if self
            .latest_full_snapshot_slot_advanced_since_clean
            .swap(false, Ordering::Acquire)
            && let Some(latest_full_snapshot_slot) = self.latest_full_snapshot_slot()
        {
            self.zero_lamport_accounts_to_purge_after_full_snapshot
                .retain(|(slot, pubkey)| {
                    let is_candidate_for_clean = max_clean_root_inclusive
                        .is_none_or(|max_clean_root_inclusive| max_clean_root_inclusive >= *slot)
                        && latest_full_snapshot_slot >= *slot;
                    if is_candidate_for_clean {
                        insert_candidate(*pubkey, true);
                    }
                    !is_candidate_for_clean
                });

            let last_swept_full_snapshot_slot =
                self.last_swept_full_snapshot_slot.load(Ordering::Relaxed);
            let (added_to_shrink_count, sweep_us) = measure_us!(self.sweep_slots_after_snapshot(
                last_swept_full_snapshot_slot,
                latest_full_snapshot_slot
            ));
            timings.zero_lamport_single_ref_slots_added_to_shrink_count += added_to_shrink_count;
            timings.zero_lamport_sweep_us += sweep_us;
        }
```

**File:** accounts-db/src/accounts_db.rs (L1725-1759)
```rust
    fn sweep_slots_after_snapshot(
        &self,
        last_swept_full_snapshot_slot: Slot,
        latest_full_snapshot_slot: Slot,
    ) -> u64 {
        let start = last_swept_full_snapshot_slot.saturating_add(1);

        let mut added_to_shrink_count = 0;
        {
            // Held for the scan. Safe because the only paths that take this lock in production
            // validator code run in earlier/later phases of the same AccountsBackgroundService
            // iteration, never concurrently with clean_accounts.
            let mut shrink_candidates = self.shrink_candidate_slots.lock().unwrap();
            for slot in start..=latest_full_snapshot_slot {
                if let Some(store) = self.storage.get_slot_storage_entry(slot) {
                    if store.has_only_tombstones() {
                        // Now just contains tombstones and no live index entries: purge
                        self.purge_dead_slots_from_storage(
                            iter::once(&slot),
                            &self.clean_accounts_stats.purge_stats,
                        );
                    } else if self.is_shrinking_productive(&store)
                        && self.is_candidate_for_shrink(&store)
                        && shrink_candidates.insert(slot)
                    {
                        added_to_shrink_count += 1;
                    }
                }
            }
        }

        self.last_swept_full_snapshot_slot
            .store(latest_full_snapshot_slot, Ordering::Relaxed);
        added_to_shrink_count
    }
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
