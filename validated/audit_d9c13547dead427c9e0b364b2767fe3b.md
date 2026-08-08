### Title
Cleaning of zero-lamport accounts stalls indefinitely if the full-snapshot slot lags, causing unbounded storage/index growth - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::clean_accounts` gates the purge of zero-lamport accounts on `latest_full_snapshot_slot`, a value that is only advanced when a full snapshot archive actually completes. This mirrors the `DelFiPrice` anchor-price bug class: a safety gate depends on an externally-updated reference value, and if that reference is not advanced quickly enough, the protected operation (here, cleaning/purging) is perpetually deferred, letting garbage accumulate without bound.

### Finding Description
`filter_zero_lamport_clean_for_incremental_snapshots` refuses to purge a zero-lamport account whenever its slot is greater than `latest_full_snapshot_slot`, explicitly to avoid breaking incremental-snapshot correctness: [1](#0-0) 

Accounts that cannot yet be purged are placed on `zero_lamport_accounts_to_purge_after_full_snapshot`, and are only reconsidered for cleaning once `latest_full_snapshot_slot` advances past them: [2](#0-1) 

`can_purge_zero_lamport_single_ref_after_shrink` applies the same gate to shrink, so zero-lamport single-ref accounts also stay "alive" from shrink's perspective until the full-snapshot slot catches up: [3](#0-2) 

`latest_full_snapshot_slot` is only updated when a full snapshot archive completes (via `set_latest_full_snapshot_slot`, called from `runtime/src/accounts_background_service.rs`), which happens at `full_snapshot_archive_interval` (default 100,000 slots) — an interval controlled by validator/operator configuration and the actual completion of packaging/uploading a full snapshot: [4](#0-3) 

This is structurally identical to the DelFiPrice bug: a bound/gate (`anchor` price / `latest_full_snapshot_slot`) is supposed to track the live state, but if the reference value is not advanced promptly — because the interval is large, snapshot generation is slow, disk/network I/O for archiving lags, or the background service is busy — every zero-lamport account created above the last full-snapshot slot piles up on `zero_lamport_accounts_to_purge_after_full_snapshot` and remains fully counted against `alive_bytes`/`alive_bytes_after_shrink`, defeating both clean and shrink for that data.

### Impact Explanation
While a full snapshot is pending (which, at the default 100,000-slot interval, or under any operational slowdown of snapshot packaging, can be a very long time), every zero-lamport account touched during that window is retained in the accounts index and on-disk storage instead of being reclaimed. Because `is_shrinking_productive`/`alive_bytes_after_shrink` also treat these accounts as alive, storages containing them are skipped by shrink. The end effect is unbounded growth of `zero_lamport_accounts_to_purge_after_full_snapshot`, larger in-memory index (`accounts_index`) size, and larger on-disk storage footprint than necessary — i.e., "disproportionate storage and CPU cost" from repeatedly rescanning/retaining dead entries during clean and shrink cycles.

### Likelihood Explanation
This requires no attacker action beyond normal high write/burn activity that creates many zero-lamport accounts (e.g., closing many token/program accounts) combined with a long or stalled interval between full snapshots — both are realistic, unprivileged conditions (default full-snapshot interval is 100,000 slots, and snapshot archive generation is I/O-heavy and can itself be delayed under load). This is a genuine capitalization/liveness concern for AccountsDB cleaning, not a theoretical one, since the code comments themselves acknowledge the tradeoff was intentionally made to protect incremental snapshot correctness, without a compensating bound on the deferred-purge list's growth or interval fallback.

### Recommendation
Add a bound/backpressure mechanism so that the deferred zero-lamport purge list (`zero_lamport_accounts_to_purge_after_full_snapshot`) and the corresponding non-shrinkable storages cannot grow without limit while waiting for `latest_full_snapshot_slot` to advance. Options: track and alert on the size/duration of this deferred set, allow an incremental-snapshot-safe pathway to reclaim entries once an incremental snapshot has itself progressed past a slot even without a new full snapshot, or trigger an out-of-band full snapshot generation ahead of the configured interval if the deferred-purge set exceeds a threshold.

### Proof of Concept
1. Configure a validator/test with `full_snapshot_archive_interval` set high (default is 100,000 slots) or simulate a stalled/slow snapshot generation.
2. Repeatedly create and zero-out (close) many accounts across slots above the current `latest_full_snapshot_slot`.
3. Call `clean_accounts` repeatedly (as in `runtime/src/serde_snapshot/tests.rs` `test_clean_...` and `accounts_db/src/accounts_db/tests/impl.rs::test_shrink_zero_lamport_single_ref_account`, which already demonstrate the gating behavior via `set_latest_full_snapshot_slot`/`latest_full_snapshot_slot`) and observe that the zero-lamport accounts remain in the index/storage, `zero_lamport_accounts_to_purge_after_full_snapshot` grows, and `shrink_slot_forced` reports these accounts as alive (`accounts.contains(&pubkey_zero)` stays true) — mirroring the referenced test scenario at: [5](#0-4) 
until a full snapshot slot is advanced past the relevant slots.

### Citations

**File:** accounts-db/src/accounts_db.rs (L1685-1712)
```rust
        // Cleaning up zero lamport accounts is gated by a full snapshot because they need to be
        // retained for incremental snapshots. Once a full snapshot occurs, drain the list and
        // search for newly shrinkable storages.
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

**File:** accounts-db/src/accounts_db.rs (L2359-2369)
```rust
                // Do *not* purge zero-lamport accounts if the slot is greater than the last full
                // snapshot slot.  Since we're `retain`ing the accounts-to-purge, I felt creating
                // the `cannot_purge` variable made this easier to understand.  Accounts that do
                // not get purged here are added to a list so they be considered for purging later
                // (i.e. after the next full snapshot).
                assert!(account_info.is_zero_lamport());
                let cannot_purge = *slot > latest_full_snapshot_slot.unwrap();
                if cannot_purge {
                    self.zero_lamport_accounts_to_purge_after_full_snapshot
                        .insert((*slot, *pubkey));
                }
```

**File:** accounts-db/src/accounts_db.rs (L5007-5023)
```rust
    /// Can zero lamport single ref accounts in `slot` be purged?
    fn can_purge_zero_lamport_single_ref_after_shrink(&self, slot: Slot) -> bool {
        self.latest_full_snapshot_slot()
            .is_none_or(|latest_full_snapshot_slot| slot <= latest_full_snapshot_slot)
    }

    /// Returns the expected alive bytes after shrinking `store`.
    pub(crate) fn alive_bytes_after_shrink(&self, store: &AccountStorageEntry) -> usize {
        // Obsolete accounts are already excluded from `store.alive_bytes()`.
        // Zero-lamport single-ref accounts are counted as alive until shrink can purge them,
        // which is gated by the latest full snapshot slot.
        if self.can_purge_zero_lamport_single_ref_after_shrink(store.slot()) {
            store.alive_bytes_exclude_zero_lamport_single_ref_accounts()
        } else {
            store.alive_bytes()
        }
    }
```

**File:** snapshots/src/snapshot_config.rs (L9-12)
```rust
pub const DEFAULT_FULL_SNAPSHOT_ARCHIVE_INTERVAL_SLOTS: NonZeroU64 =
    NonZeroU64::new(100_000).unwrap();
pub const DEFAULT_INCREMENTAL_SNAPSHOT_ARCHIVE_INTERVAL_SLOTS: NonZeroU64 =
    NonZeroU64::new(100).unwrap();
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L1362-1389)
```rust
        if let Some(latest_full_snapshot_slot) = latest_full_snapshot_slot {
            accounts.set_latest_full_snapshot_slot(latest_full_snapshot_slot);
        }

        // Shrink the slot. The behavior on the zero lamport account will depend on `latest_full_snapshot_slot`.
        accounts.shrink_slot_forced(slot);

        assert!(
            accounts.storage.get_slot_storage_entry(slot).is_some(),
            "{latest_full_snapshot_slot:?}"
        );

        let expected_alive_count = if latest_full_snapshot_slot.unwrap_or(Slot::MAX) < slot {
            // zero lamport account should NOT be dead in the database
            assert!(
                accounts.contains(&pubkey_zero),
                "{latest_full_snapshot_slot:?}"
            );
            2
        } else {
            // zero lamport account should be dead in the database
            assert!(
                !accounts.contains(&pubkey_zero),
                "{latest_full_snapshot_slot:?}"
            );
            // the zero lamport account should be marked as dead
            1
        };
```
