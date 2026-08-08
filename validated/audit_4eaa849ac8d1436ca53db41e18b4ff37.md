Based on my investigation, I found a genuine analog in `AccountsDb`'s `latest_full_snapshot_slot` mechanism, which governs a cache-invalidation-style contract that is only partially enforced.

### Title
`set_latest_full_snapshot_slot` allows regression of the gating value, silently invalidating the `last_swept_full_snapshot_slot` invariant relied upon by clean's zero-lamport sweep - (File: accounts-db/src/accounts_db.rs)

### Summary
`AccountsDb::latest_full_snapshot_slot` is the "registry-like" pointer that gates whether zero-lamport / zero-lamport-single-ref (ZLSR) accounts can be purged, mirroring the reported pattern of a version pointer whose updates are not checked for consistency against a derived cache value (`last_swept_full_snapshot_slot`).

### Finding Description
`set_latest_full_snapshot_slot` unconditionally overwrites the pointer with no monotonicity check: [1](#0-0) 

In contrast, the companion setter `set_last_swept_full_snapshot_slot` — which records how far the "sweep" of zero-lamport slots has progressed — explicitly asserts `last_swept <= latest`: [2](#0-1) 

This shows the invariant `last_swept_full_snapshot_slot <= latest_full_snapshot_slot` is intended to always hold, but it is enforced only from the "narrow" setter's side, not from `set_latest_full_snapshot_slot` itself. If `latest_full_snapshot_slot` is ever set backward (or to a value smaller than the current `last_swept_full_snapshot_slot`) after `last_swept_full_snapshot_slot` has already advanced past it, the invariant silently breaks — exactly analogous to the reported bug where the "registry" pointer is updated without validating consistency with its dependent cache.

The consumer of this pair, `sweep_slots_after_snapshot`, computes the scan range as `[last_swept_full_snapshot_slot + 1, latest_full_snapshot_slot]`: [3](#0-2) 

If `latest_full_snapshot_slot` regresses below `last_swept_full_snapshot_slot`, the range becomes empty/inverted (`start > latest_full_snapshot_slot`), so the `for slot in start..=latest_full_snapshot_slot` loop silently does nothing, and then `last_swept_full_snapshot_slot` is unconditionally stored back to the now-smaller `latest_full_snapshot_slot` value: [4](#0-3) 

That store itself now moves `last_swept_full_snapshot_slot` backward, meaning slots that were previously already swept and cleared for shrink eligibility become "unswept" again from the tracker's perspective, while `zero_lamport_accounts_to_purge_after_full_snapshot` and `filter_zero_lamport_clean_for_incremental_snapshots` continue to gate purge eligibility strictly by comparing against the (now regressed) `latest_full_snapshot_slot`: [5](#0-4) 

The explicit safety comment in `filter_zero_lamport_clean_for_incremental_snapshots` documents exactly why this pointer must stay consistent with what has already been snapshotted: [6](#0-5) 

If it is allowed to regress, accounts that were already purged (because they were within the old, larger `latest_full_snapshot_slot`) are no longer protected by the described guard for slots between the new smaller value and the old value — the danger window the comment describes is exactly what an inconsistent update to this "registry" value can reopen.

### Impact Explanation
If `latest_full_snapshot_slot` regresses relative to prior state, this can cause the exact class of corruption the code comment already warns about: an account whose zero-lamport update was purged from the accounts index/storage because it was covered by a full snapshot at slot N, then later an incremental snapshot taken relative to a regressed full-snapshot-slot < N would not contain that account's zero-lamport update, while the "full" snapshot base doesn't either (if it was already purged) — leading to reconstructing an account with a stale, non-zero balance on restart. This matches the "silent balance change" / "honest-node snapshot-vs-replay mismatch" impact categories.

### Likelihood Explanation
All current production call sites (`ledger/src/bank_forks_utils.rs`, `runtime/src/accounts_background_service.rs`, `runtime/src/snapshot_bank_utils.rs`) call `set_latest_full_snapshot_slot` with monotonically increasing slots (bank slot / full snapshot archive slot), so under normal validator operation this path is not reachable. [7](#0-6) [8](#0-7)  The missing check is a latent correctness gap in the function's contract rather than something exploitable through a currently reachable code path, so likelihood is low absent a config/replay scenario (e.g., ledger-tool/warp tooling or future code changes) that calls this setter with a non-monotonic value.

### Recommendation
Add an assertion/guard in `set_latest_full_snapshot_slot` mirroring the one in `set_last_swept_full_snapshot_slot`, rejecting (or clamping) updates where the new slot is less than the current value, and ideally less than `last_swept_full_snapshot_slot`. This turns the currently implicit, one-sided invariant into an enforced consistency check at the point where the "registry" pointer is mutated, matching the report's short-term recommendation to add consistency checks when a governing pointer is updated.

### Proof of Concept
1. Call `accounts_db.set_latest_full_snapshot_slot(100)`.
2. Call `accounts_db.set_last_swept_full_snapshot_slot(100)` (passes the `<=` assert).
3. Call `accounts_db.set_latest_full_snapshot_slot(50)` — succeeds with no error, silently violating `last_swept_full_snapshot_slot (100) <= latest_full_snapshot_slot (50)`.
4. Trigger `clean_accounts` — `sweep_slots_after_snapshot(100, 50)` computes `start = 101`, iterates an empty range, then stores `last_swept_full_snapshot_slot = 50`, and `filter_zero_lamport_clean_for_incremental_snapshots`/purge-after-snapshot logic now use the regressed slot 50 as the cutoff for accounts that may already have been purged under the old cutoff of 100, reopening the incremental-snapshot corruption window described in the code's own comment. This was not verified end-to-end with an actual snapshot roundtrip; the analysis is based on tracing the guarded vs. unguarded setters and their consumers.

### Citations

**File:** accounts-db/src/accounts_db.rs (L1717-1758)
```rust
    /// Loop through slots in `[last_swept_full_snapshot_slot + 1, latest_full_snapshot_slot]` and
    /// re-examine each storage now that a full snapshot has advanced past its slot:
    /// 1) if it holds only tombstones, purge it directly; or
    /// 2) if its dead zero-lamport accounts made it shrinkable, add it to the shrink candidates.
    ///
    /// Advances `last_swept_full_snapshot_slot` to `latest_full_snapshot_slot` on completion.
    ///
    /// Returns the count of storages that were added to the shrink candidates set.
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
```

**File:** accounts-db/src/accounts_db.rs (L2292-2311)
```rust
    /// During clean, some zero-lamport accounts that are marked for purge should *not* actually
    /// get purged.  Filter out those accounts here by removing them from 'candidates'.
    /// Candidates may contain entries with empty slots list in CleaningInfo.
    /// The function removes such entries from 'candidates'.
    ///
    /// When using incremental snapshots, do not purge zero-lamport accounts if the slot is higher
    /// than the latest full snapshot slot.  This is to protect against the following scenario:
    ///
    ///   ```text
    ///   A full snapshot is taken, including account 'alpha' with a non-zero balance.  In a later slot,
    ///   alpha's lamports go to zero.  Eventually, cleaning runs.  Without this change,
    ///   alpha would be cleaned up and removed completely. Finally, an incremental snapshot is taken.
    ///
    ///   Later, the incremental and full snapshots are used to rebuild the bank and accounts
    ///   database (e.x. if the node restarts).  The full snapshot _does_ contain alpha
    ///   and its balance is non-zero.  However, since alpha was cleaned up in a slot after the full
    ///   snapshot slot (due to having zero lamports), the incremental snapshot would not contain alpha.
    ///   Thus, the accounts database will contain the old, incorrect info for alpha with a non-zero
    ///   balance.  Very bad!
    ///   ```
```

**File:** accounts-db/src/accounts_db.rs (L2359-2370)
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
                !cannot_purge
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
