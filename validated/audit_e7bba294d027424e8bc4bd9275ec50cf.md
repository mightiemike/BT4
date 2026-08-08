### Title
`last_swept_full_snapshot_slot` initialized to `0` instead of a "not yet swept" sentinel causes an unbounded, disproportionately expensive slot sweep on the first full snapshot after startup - (File: `accounts-db/src/accounts_db.rs`)

### Summary
This is a Sherlock-style "incorrect initialization of a time/state-tracking field" bug, analogous to the Astaria `firstBidTime` issue: a field meant to record "the last point up to which work has already been done" is eagerly initialized to `0` at construction time instead of being seeded from the actual current state. In `AccountsDb::new_with_config`, `last_swept_full_snapshot_slot` is hard-coded to `AtomicU64::new(0)` [1](#0-0)  instead of being left uninitialized/sentineled until the real "already swept up to here" boundary is known. Just like the auction's `firstBidTime` being stamped at creation time caused the duration window to start counting from the wrong reference point, `last_swept_full_snapshot_slot` starting at `0` causes the "already processed" boundary to be wrong on the very first sweep, and the resulting range that must be swept balloons unexpectedly.

### Finding Description
`last_swept_full_snapshot_slot` tracks "slots up to which we already re-examined storages for zero-lamport-single-ref shrink/purge eligibility after a full snapshot slot advance" [2](#0-1) . It is only advanced by `sweep_slots_after_snapshot`, which iterates every slot in `(last_swept_full_snapshot_slot, latest_full_snapshot_slot]` calling `self.storage.get_slot_storage_entry(slot)` for each one [3](#0-2) , and by `set_last_swept_full_snapshot_slot` [4](#0-3) .

The only production caller that "seeds" `last_swept_full_snapshot_slot` to a sane starting boundary is the snapshot-restore path in `bank_forks_utils.rs`, which explicitly sets it to the loaded full snapshot's slot right after `set_latest_full_snapshot_slot` so that "the first full snapshot only triggers cleaning ... between the previous full snapshot and the new full snapshot" [5](#0-4) .

However, `AccountsBackgroundService::handle_snapshot_request` — which runs on *every* full snapshot produced during normal validator operation — calls only `set_latest_full_snapshot_slot(snapshot_root_bank.slot())` and never calls `set_last_swept_full_snapshot_slot` [6](#0-5) . `set_latest_full_snapshot_slot` merely records the new slot and flags that clean needs to re-sweep [7](#0-6) ; it never touches `last_swept_full_snapshot_slot`.

Consequently, on any AccountsDb instance that never goes through the `bank_forks_utils.rs` snapshot-restore seeding call (e.g. a validator/ledger-tool process that boots from genesis and replays the ledger instead of restoring from a downloaded snapshot archive, or any caller that constructs `AccountsDb` fresh and later calls `set_latest_full_snapshot_slot` directly, such as `bank_to_incremental_snapshot_archive` [8](#0-7) ), `last_swept_full_snapshot_slot` remains at its construction-time default of `0`. The very next `clean_accounts()` call after the first full-snapshot slot advance then invokes `sweep_slots_after_snapshot(0, latest_full_snapshot_slot)` [9](#0-8) , forcing a full scan over `[1, latest_full_snapshot_slot]` — potentially tens of millions of slot lookups against `self.storage` — in a single synchronous pass, all while holding `shrink_candidate_slots` locked for the duration of the loop [10](#0-9) .

This mirrors the Astaria root cause exactly: a state field that should track "the last point actually processed" is instead defaulted to a value (`0`/creation time) that does not correspond to any real event, silently expanding (here) or shrinking (there) the effective window of an interval-bound operation.

### Impact Explanation
When triggered on a validator/ledger-tool process that has processed a very large number of slots before its first full-snapshot completion without ever calling `set_last_swept_full_snapshot_slot` (e.g., full ledger replay from genesis with snapshots freshly enabled, or any code path invoking `bank_to_incremental_snapshot_archive`/direct `set_latest_full_snapshot_slot` calls on a long-lived `AccountsDb`), `clean_accounts()` performs a synchronous sweep across the entire historical slot range instead of the intended narrow window between two full snapshots. This is a disproportionate CPU cost issue: the `AccountsBackgroundService` main loop thread is blocked for the length of this sweep, delaying subsequent clean/shrink/snapshot-request processing, and the `shrink_candidate_slots` mutex is held for that entire scan.

### Likelihood Explanation
This requires a specific but realistic operational sequence: an `AccountsDb` that accumulates a large slot range before its `last_swept_full_snapshot_slot` is first properly seeded via the snapshot-restore path. This occurs whenever a node does not restore from a downloaded snapshot archive (full ledger replay from genesis, or programmatic snapshot creation via `bank_to_incremental_snapshot_archive`/ledger-tool workflows), so it is a legitimate, honest-operator scenario, not a normal validator boot condition, which limits (but does not eliminate) likelihood.

### Recommendation
Seed `last_swept_full_snapshot_slot` from the actual current state instead of hard-coding `0` in `AccountsDb::new_with_config`: either initialize it lazily to the value of `latest_full_snapshot_slot` the first time `set_latest_full_snapshot_slot`/`sweep_slots_after_snapshot` observes it unset, or make `handle_snapshot_request`'s call to `set_latest_full_snapshot_slot` also correctly seed `last_swept_full_snapshot_slot` on the very first full-snapshot slot advance for a given `AccountsDb` instance (mirroring what `bank_forks_utils.rs` already does for the snapshot-restore path).

### Proof of Concept
1. Construct a fresh `AccountsDb` (e.g., via `AccountsDb::new_with_config`), which leaves `last_swept_full_snapshot_slot = 0` [1](#0-0) , without calling `set_last_swept_full_snapshot_slot`.
2. Store/root accounts across a very large number of slots (simulating a long ledger replay from genesis).
3. Call `set_latest_full_snapshot_slot(latest_slot)` directly (as `bank_to_incremental_snapshot_archive` and `handle_snapshot_request` do) without a matching `set_last_swept_full_snapshot_slot` call.
4. Call `clean_accounts(...)`; observe `sweep_slots_after_snapshot` iterates the full `[1, latest_slot]` range [11](#0-10)  instead of a small window since the previous full snapshot, demonstrating the disproportionate scan cost caused by the `0` initialization.

### Citations

**File:** accounts-db/src/accounts_db.rs (L972-974)
```rust
    /// The full snapshot slot we last swept for zero-lamport-single-ref shrink
    /// eligibility.
    last_swept_full_snapshot_slot: AtomicU64,
```

**File:** accounts-db/src/accounts_db.rs (L1142-1142)
```rust
            last_swept_full_snapshot_slot: AtomicU64::new(0),
```

**File:** accounts-db/src/accounts_db.rs (L1704-1711)
```rust
            let last_swept_full_snapshot_slot =
                self.last_swept_full_snapshot_slot.load(Ordering::Relaxed);
            let (added_to_shrink_count, sweep_us) = measure_us!(self.sweep_slots_after_snapshot(
                last_swept_full_snapshot_slot,
                latest_full_snapshot_slot
            ));
            timings.zero_lamport_single_ref_slots_added_to_shrink_count += added_to_shrink_count;
            timings.zero_lamport_sweep_us += sweep_us;
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

**File:** accounts-db/src/accounts_db.rs (L5675-5680)
```rust
    /// Sets the latest full snapshot slot to `slot`
    pub fn set_latest_full_snapshot_slot(&self, slot: Slot) {
        *self.latest_full_snapshot_slot.lock_write() = Some(slot);
        self.latest_full_snapshot_slot_advanced_since_clean
            .store(true, Ordering::Release);
    }
```

**File:** accounts-db/src/accounts_db.rs (L5682-5695)
```rust
    /// Marks slots <= slot as already swept for zero-lamport-single-ref shrink eligibility
    pub fn set_last_swept_full_snapshot_slot(&self, slot: Slot) {
        // Prior to setting this, the latest full snapshot slot must be set, and
        // last_swept_full_snapshot_slot value must be less than or equal to it.
        assert!(
            self.latest_full_snapshot_slot()
                .is_some_and(|snapshot_slot| slot <= snapshot_slot),
            "last swept full snapshot slot {slot} cannot be greater than latest full snapshot \
             slot {:?}",
            self.latest_full_snapshot_slot()
        );
        self.last_swept_full_snapshot_slot
            .store(slot, Ordering::Relaxed);
    }
```

**File:** ledger/src/bank_forks_utils.rs (L266-277)
```rust
    if snapshot_config.should_generate_snapshots() {
        bank.rc
            .accounts
            .accounts_db
            .set_latest_full_snapshot_slot(full_snapshot_archive_info.slot());
        // Set the last swept slot so the first full snapshot only triggers
        // cleaning of zero lamport single ref accounts between the previous
        // full snapshot and the new full snapshot
        bank.rc
            .accounts
            .accounts_db
            .set_last_swept_full_snapshot_slot(full_snapshot_archive_info.slot());
```

**File:** runtime/src/accounts_background_service.rs (L238-247)
```rust
        if snapshot_kind.is_full_snapshot() {
            // The latest full snapshot slot is what accounts-db uses to properly handle
            // zero lamport accounts.  We are handling a full snapshot request here, and
            // since taking a snapshot is not allowed to fail, we can update accounts-db now.
            snapshot_root_bank
                .rc
                .accounts
                .accounts_db
                .set_latest_full_snapshot_slot(snapshot_root_bank.slot());
        }
```

**File:** runtime/src/snapshot_bank_utils.rs (L769-774)
```rust
    // set accounts-db's latest full snapshot slot here to ensure zero lamport
    // accounts are handled properly.
    bank.rc
        .accounts
        .accounts_db
        .set_latest_full_snapshot_slot(full_snapshot_slot);
```
