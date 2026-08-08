### Title
Ancient-append-vec squash silently drops carry-forward tombstones for not-yet-purgeable zero-lamport accounts, permanently losing the account's zeroing from disk - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
When `AccountsDb` shrinks/squashes "ancient" append vecs it calls `shrink_collect`, which for zero-lamport single-ref accounts either purges them (if the slot is older than or equal to `latest_full_snapshot_slot`) or keeps them as a "tombstone" byte record in `ShrinkCollect::tombstones_to_carry_forward` so a later incremental snapshot can still observe and propagate the zeroing [1](#0-0) . The ancient-squash write path (`finish_combine_ancient_slots_packed_internal` → `PackedAncientStorage::pack` → `write_packed_storages`) never forwards `tombstones_to_carry_forward` into the packed output; it only packs `one_ref` / `many_refs_this_is_newest_alive` accounts [2](#0-1) . The code explicitly documents this gap and only checks it with a `debug_assert!`, which is compiled out in release builds:

```
// Ancient squash only runs on slots far older than the latest full snapshot, where
// tombstones are purgeable and `shrink_collect` drops them rather than carrying them
// forward. The squash write path has no tombstone handling, so a non-empty list here
// would be silently lost; assert the invariant at the point that loss would occur.
debug_assert!(
    shrink_collect.tombstones_to_carry_forward.is_empty(),
    "ancient squash reached a carry-forward tombstone at slot {slot}",
);
``` [3](#0-2) 

### Finding Description
`shrink_collect` decides per-account whether a zero-lamport single-ref (ZLSR) account can be fully purged (index entry removed, bytes dropped) or must be retained as a tombstone, based solely on `can_purge_zero_lamport_single_ref_after_shrink(slot)` — i.e., whether `slot <= latest_full_snapshot_slot` [4](#0-3) [5](#0-4) . Both the normal shrink path (`shrink_storage`) and the ancient-squash path (`combine_ancient_slots_packed_internal`) call the *same* `shrink_collect` function [6](#0-5) , so both can, in principle, produce a non-empty `tombstones_to_carry_forward` if the slot being processed is *not yet* covered by the latest full snapshot at the moment shrink/squash runs.

The normal shrink path correctly rewrites these tombstones into the new storage via `store_tombstones` [7](#0-6) . The ancient-squash path has no equivalent handling: `calc_accounts_to_combine` and `PackedAncientStorage::pack` only operate on `alive_accounts` (`one_ref` / `many_refs_this_is_newest_alive`), never on `tombstones_to_carry_forward` [8](#0-7) . After packing, `finish_combine_ancient_slots_packed_internal` drops the old storages entirely via `remove_old_stores_shrink` [9](#0-8) , permanently discarding any tombstone bytes that were not written into the new packed storage. Because the ZLSR index entry was already unreffed/removed from the accounts index during `load_accounts_index_for_shrink` (the same step that decides an account is tombstone-eligible) [10](#0-9) , once its storage bytes are also gone, the pubkey leaves no trace anywhere in `AccountsDb` — neither as a live account nor as a zero-lamport marker.

The invariant that ancient-squash slots are always older than `latest_full_snapshot_slot` (making this scenario impossible) is enforced only by a `debug_assert!`, not a runtime check or `assert!`. In release builds (`cfg(not(debug_assertions))`, the standard build profile for validator binaries) this assertion is compiled to a no-op, so if the invariant is ever violated — e.g., a race between the caller selecting ancient slot candidates and `set_latest_full_snapshot_slot` being invoked concurrently by the snapshot-generation background service, or any code path that requests ancient combination for a slot at/above the current full-snapshot boundary — the loss happens silently with no logged error, no metric, and no panic in production.

### Impact Explanation
If this path is hit, an account that was previously funded and then zeroed out has its zero-lamport tombstone deleted from `AccountsDb` before the next full snapshot covers the slot. A subsequent incremental snapshot taken relative to the (older) full snapshot will contain no entry for that pubkey at all, because the tombstone that should have signaled "this account is now zero" is gone. When a node (or a different node) later rebuilds its bank from that full + incremental snapshot pair, it will "resurrect" the account's old non-zero balance from the full snapshot, since nothing in the incremental snapshot instructs it to zero the balance. This is exactly the "very bad" scenario already documented for the normal-shrink case [11](#0-10) , but here it can occur silently through the ancient-squash path with no assertion firing in production. The result is a silent, incorrect balance / stale account state divergence between the live-replayed state and a state rebuilt from snapshots — a capitalization/consensus-relevant discrepancy for any node that restarts from the affected snapshot pair.

### Likelihood Explanation
Ancient-slot selection for squashing is driven by slot age/count heuristics, and the current code assumes (via comment) that ancient slots are always far older than the latest full snapshot slot, which is the normal case in day-to-day operation. However, this assumption is not runtime-enforced; `latest_full_snapshot_slot` is updated asynchronously by the snapshot-generation background service, and slots eligible for ancient packing are chosen independently by the shrink/ancient background thread. I was not able to fully trace, within the remaining tool budget, the precise scheduling guarantee (if any) that prevents `combine_ancient_slots_packed` from ever being invoked on a slot at or above the current `latest_full_snapshot_slot` value at call time — this is the key uncertain point. The presence of a dedicated `debug_assert!` specifically guarding against this exact condition, with a comment stating data would be "silently lost" otherwise, indicates the developers themselves identified this as a plausible-but-supposed-to-be-prevented-elsewhere scenario, which is the hallmark of a real (if narrow) window rather than a purely theoretical one.

### Recommendation
- Replace the `debug_assert!` in `finish_combine_ancient_slots_packed_internal` with a hard runtime check (`assert!`) or, better, actively handle the case: if `tombstones_to_carry_forward` is non-empty for a slot being ancient-packed, either (a) skip squashing that slot until it is safely purgeable, or (b) extend `write_packed_storages`/`PackedAncientStorage::pack` to also carry forward tombstone bytes into the packed output, mirroring what `shrink_storage` already does with `store_tombstones`.
- Add an explicit runtime guard at ancient-slot selection time ensuring only slots `<= latest_full_snapshot_slot` (or wherever the actual safety boundary is) are eligible for ancient combination, closing the race window between slot selection and snapshot-slot advancement.
- Add a metric/counter that increments if this condition is ever encountered in production, so any real-world occurrence is observable rather than silent.

### Proof of Concept
Not independently reproduced within the given tool budget (read-only code search only; no execution environment available). The existing repository test `test_combine_ancient_slots_packed_internal` and related ancient-append-vec tests exercise the packing path but do not construct a scenario with a non-empty `tombstones_to_carry_forward` [12](#0-11) ; a concrete PoC would extend that harness to (1) create a ZLSR account in a slot selected for ancient combination, (2) set `latest_full_snapshot_slot` below that slot so `can_purge_zero_lamport_single_ref_after_shrink` returns `false`, and (3) call `combine_ancient_slots_packed_internal` in a release-mode build to observe the tombstone silently vanish (no index entry, no storage bytes, and no panic) instead of hitting the `debug_assert!`.

### Citations

**File:** accounts-db/src/accounts_db.rs (L2297-2311)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L2412-2438)
```rust
        let can_purge_zero_lamport_single_ref =
            self.can_purge_zero_lamport_single_ref_after_shrink(slot_to_shrink);
        let count = accounts.len();
        let mut alive_accounts = T::with_capacity(count, slot_to_shrink);
        let mut zero_lamport_single_ref_pubkeys = Vec::with_capacity(count);
        let mut tombstones = Vec::new();

        let mut alive = 0;
        let mut index = 0;
        let mut index_scan_returned_some_count = 0;
        let mut index_scan_returned_none_count = 0;
        let mut all_are_zero_lamports = true;
        self.accounts_index.scan(
            accounts.iter().map(|account| account.pubkey()),
            |pubkey, slots_refs| {
                let stored_account = &accounts[index];
                let mut do_populate_accounts_for_shrink = |ref_count, slot_list| {
                    if stored_account.is_zero_lamport() && ref_count == 1 {
                        // The lone instance of a zero-lamport account. A load of a zero-lamport
                        // account already reports "not found", so dropping its index entry is safe.
                        zero_lamport_single_ref_pubkeys.push(pubkey);
                        if !can_purge_zero_lamport_single_ref {
                            // Newer than the latest full snapshot: keep the bytes in storage as a
                            // tombstone so an incremental snapshot can still propagate the deletion,
                            // rather than dropping it.
                            tombstones.push(*stored_account);
                        }
```

**File:** accounts-db/src/accounts_db.rs (L2568-2585)
```rust
        // Filter and collect tombstones
        let can_purge_zero_lamport_single_ref =
            self.can_purge_zero_lamport_single_ref_after_shrink(slot);
        let mut tombstones_to_carry_forward = Vec::new();
        let tombstone_offsets = store.tombstone_offsets_read_lock();
        if !tombstone_offsets.is_empty() {
            stored_accounts.retain(|account| {
                if tombstone_offsets.contains(&account.index_info.offset()) {
                    // If we can't purge zero lamport accounts, they need to be rewritten after shrink
                    if !can_purge_zero_lamport_single_ref {
                        tombstones_to_carry_forward.push(*account);
                    }
                    false
                } else {
                    true
                }
            });
        }
```

**File:** accounts-db/src/accounts_db.rs (L2864-2873)
```rust

        let tombstone_refs: Vec<_> = shrink_collect.tombstones_to_carry_forward.iter().collect();
        let tombstone_accounts = [(slot, &tombstone_refs[..])];
        let storable_tombstones = StorableAccountsBySlot::new(slot, &tombstone_accounts, self);
        let (num_tombstones_carried_forward, tombstone_carry_forward_us) = measure_us!(
            self.store_tombstones(shrink_in_progress.new_storage(), storable_tombstones)
        );
        stats_sub.tombstone_carry_forward_us = Saturating(tombstone_carry_forward_us);
        stats_sub.num_tombstones_carried_forward =
            Saturating(num_tombstones_carried_forward as u64);
```

**File:** accounts-db/src/ancient_append_vecs.rs (L484-511)
```rust
        // for the accounts which are one ref and can be put anywhere, we want to put the accounts from the LARGEST storages at the end.
        // This causes us to keep the accounts we're re-packing from already existing ancient storages together with other normal one ref accounts.
        // The alternative could cause us to mix newly ancient slots produced by flush (containing accounts touched more recently) with previously
        // packed ancient storages which over time contained enough dead accounts that the storage needed to be shrunk by being re-packed.
        // The end result of this sort should cause older, colder accounts (previously packed into large storages and then re-packed/shrunk) to
        // be re-packed together with other older/colder accounts.
        accounts_to_combine
            .accounts_to_combine
            .sort_unstable_by_key(|a| a.written_bytes);

        // pack the accounts with 1 ref or refs > 1 but the slot we're packing is the highest alive slot for the pubkey.
        // Note the `chain` below combining the 2 types of refs.
        let pack = PackedAncientStorage::pack(
            many_refs_newest.iter().chain(
                accounts_to_combine
                    .accounts_to_combine
                    .iter()
                    .map(|shrink_collect| &shrink_collect.alive_accounts.one_ref),
            ),
            tuning.ideal_storage_size,
        );

        if pack.len() > accounts_to_combine.target_slots_sorted.len() {
            // Not enough slots to contain the accounts we are trying to pack.
            return;
        }

        let write_ancient_accounts = self.write_packed_storages(&accounts_to_combine, pack);
```

**File:** accounts-db/src/ancient_append_vecs.rs (L734-741)
```rust
            // Ancient squash only runs on slots far older than the latest full snapshot, where
            // tombstones are purgeable and `shrink_collect` drops them rather than carrying them
            // forward. The squash write path has no tombstone handling, so a non-empty list here
            // would be silently lost; assert the invariant at the point that loss would occur.
            debug_assert!(
                shrink_collect.tombstones_to_carry_forward.is_empty(),
                "ancient squash reached a carry-forward tombstone at slot {slot}",
            );
```

**File:** accounts-db/src/ancient_append_vecs.rs (L758-763)
```rust
            self.remove_old_stores_shrink(
                &shrink_collect,
                &self.shrink_ancient_stats.shrink_stats,
                shrink_in_progress,
                true,
            );
```

**File:** accounts-db/src/ancient_append_vecs.rs (L803-812)
```rust
        let mut accounts_to_combine = accounts_per_storage
            .iter_mut()
            .map(|(info, unique_accounts)| {
                self.shrink_collect::<ShrinkCollectAliveSeparatedByRefs<'_>>(
                    &info.storage,
                    unique_accounts,
                    &self.shrink_ancient_stats.shrink_stats,
                )
            })
            .collect::<Vec<_>>();
```

**File:** accounts-db/src/ancient_append_vecs.rs (L3397-3465)
```rust
    #[test]
    fn test_combine_ancient_slots_packed_internal() {
        let can_randomly_shrink = false;
        let alive = true;
        for num_slots in 0..4 {
            for max_ancient_slots in 0..4 {
                let (db, slot1) = create_db_with_storages_and_index(alive, num_slots, None);
                let original_stores = (0..num_slots)
                    .filter_map(|slot| db.storage.get_slot_storage_entry((slot as Slot) + slot1))
                    .collect::<Vec<_>>();
                let original_results = original_stores
                    .iter()
                    .map(|store| (store.slot(), db.get_unique_accounts_from_storage(store)))
                    .collect::<Vec<_>>();
                let original_results_all_accounts = vec_unique_to_accounts(&original_results, &db);

                let tuning = PackedAncientStorageTuning {
                    percent_of_alive_shrunk_data: 0,
                    max_ancient_slots,
                    can_randomly_shrink,
                    ideal_storage_size: NonZeroU64::new(get_ancient_append_vec_capacity()).unwrap(),
                    ..default_tuning()
                };
                db.combine_ancient_slots_packed_internal(
                    (0..num_slots).map(|slot| (slot as Slot) + slot1).collect(),
                    tuning,
                    &mut SquashStatsSub::default(),
                );
                let storage = db.storage.get_slot_storage_entry(slot1);
                if num_slots == 0 {
                    assert!(storage.is_none());
                    continue;
                }
                // any of the several slots could have been chosen to be reused
                let active_slots = (0..num_slots)
                    .filter_map(|slot| db.storage.get_slot_storage_entry((slot as Slot) + slot1))
                    .count();
                let mut expected_slots = (max_ancient_slots / 2).min(num_slots);
                if max_ancient_slots >= num_slots {
                    expected_slots = num_slots;
                } else if max_ancient_slots == 0 || num_slots > 0 && expected_slots == 0 {
                    expected_slots = 1;
                }
                assert_eq!(
                    active_slots, expected_slots,
                    "slots: {num_slots}, max_ancient_slots: {max_ancient_slots}, alive: {alive}"
                );
                assert_eq!(
                    expected_slots,
                    db.storage.all_slots().len(),
                    "slots: {num_slots}, max_ancient_slots: {max_ancient_slots}"
                );

                let stores = (0..num_slots)
                    .filter_map(|slot| db.storage.get_slot_storage_entry((slot as Slot) + slot1))
                    .collect::<Vec<_>>();
                let results = stores
                    .iter()
                    .map(|store| (store.slot(), db.get_unique_accounts_from_storage(store)))
                    .collect::<Vec<_>>();
                let all_accounts = get_all_accounts(&db, slot1..(slot1 + num_slots as Slot));
                compare_all_accounts(&original_results_all_accounts, &all_accounts);
                compare_all_accounts(
                    &vec_unique_to_accounts(&results, &db),
                    &get_all_accounts(&db, slot1..(slot1 + num_slots as Slot)),
                );
            }
        }
    }
```
