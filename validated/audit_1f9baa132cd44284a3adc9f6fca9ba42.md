### Title
Ancient-squash tombstone-loss invariant (`debug_assert!(shrink_collect.tombstones_to_carry_forward.is_empty())`) can be violated because ancient-slot eligibility is epoch-based, not full-snapshot-based - (File: `accounts-db/src/ancient_append_vecs.rs`)

### Summary
`finish_combine_ancient_slots_packed_internal` asserts that `shrink_collect.tombstones_to_carry_forward` is always empty for slots being ancient-packed, based on the comment's assumption that "ancient squash only runs on slots far older than the latest full snapshot." However, the ancient-slot selection path (`shrink_ancient_slots` → `get_oldest_non_ancient_slot`/`get_sorted_potential_ancient_slots`) determines eligibility purely from `epoch_schedule`/slot age, with no dependency on `latest_full_snapshot_slot`. Tombstone carry-forward, by contrast, is gated solely by `can_purge_zero_lamport_single_ref_after_shrink`, which compares the slot to `latest_full_snapshot_slot`. If the full-snapshot slot lags behind the ancient-slot boundary, the two independent conditions can disagree, and the debug_assert's precondition is violated.

### Finding Description
`shrink_ancient_slots` selects ancient candidates using only `get_oldest_non_ancient_slot(epoch_schedule)` [1](#0-0) , feeding into `get_sorted_potential_ancient_slots` which just filters `self.storage.slots_less_than(oldest_non_ancient_slot)` [2](#0-1) . Neither function references `latest_full_snapshot_slot`.

Separately, whether a zero-lamport single-ref account is dropped or carried forward as a tombstone during `shrink_collect` is decided entirely by `can_purge_zero_lamport_single_ref_after_shrink(slot)`, which is a comparison against `latest_full_snapshot_slot` [3](#0-2)  and again in the tombstone-offset filtering path [4](#0-3) . This same `shrink_collect` is used both for regular shrink and for ancient packing via `get_unique_accounts_from_storage_for_combining_ancient_slots` → `calc_accounts_to_combine` [5](#0-4) .

`finish_combine_ancient_slots_packed_internal` then relies on the (undocumented-in-code, comment-only) assumption that ancient slots are always older than the full snapshot slot, so `tombstones_to_carry_forward` must be empty: [6](#0-5) 
The write path (`write_packed_storages`/`PackedAncientStorage::pack`) only packs `alive_accounts`, never `tombstones_to_carry_forward` - there is no tombstone-write step in the ancient-packing flow analogous to `store_tombstones` used in the regular shrink path [7](#0-6) . So if the assert's precondition is violated in a release build (where `debug_assert!` is a no-op), the zero-lamport tombstone accounts are silently discarded from the new packed storage while remaining logically "still owed" to the incremental-snapshot mechanism, exactly the scenario `filter_zero_lamport_clean_for_incremental_snapshots`'s doc comment warns is "Very bad!" [8](#0-7) .

Because slot age (epoch-schedule-driven) and `latest_full_snapshot_slot` (background-service-driven, advanced only when a full snapshot completes) are two independently-progressing values, there is no code-level guarantee that `latest_full_snapshot_slot >= oldest_non_ancient_slot` at the time `shrink_ancient_slots` runs. Root causes for divergence include configuring a full-snapshot interval larger than (or comparable to) the epoch length, or full-snapshot generation being delayed/slow relative to slot progression - conditions attainable through ordinary sustained transaction load creating many small, later-closed accounts in old slots (exactly the tombstone-producing pattern demonstrated in `test_shrink_collect_carries_forward_existing_tombstones` [9](#0-8) ), without needing any validator/operator/leader privilege beyond normal account creation/closure activity.

### Impact Explanation
In a debug/test build, hitting this condition causes `debug_assert!` to panic, crashing the accounts-background thread (node panic / liveness impact). In a release build, the assert is compiled out, and the tombstone accounts are silently dropped from the packed ancient storage - this is a Agave "honest-node snapshot-vs-replay mismatch" / silent state-loss category: a zero-lamport account whose presence in prior full snapshots required it be explicitly zeroed out via an incremental-snapshot-visible tombstone instead simply disappears from the packed storage, breaking the guarantee that incremental snapshots can propagate the deletion for accounts present in the full snapshot.

### Likelihood Explanation
Requires the full-snapshot slot to lag behind the epoch-based ancient-slot threshold at the moment `shrink_ancient_slots` runs - a validator/network-configuration-dependent precondition (full-snapshot interval vs. epoch length, or full-snapshot cadence delay) rather than something purely under attacker control. The attacker's only necessary contribution is ordinary, unprivileged account churn (create small accounts in old slots, then close them) to produce the zero-lamport single-ref tombstones that get picked up by `shrink_collect`. Given SECURITY.md excludes issues "fixable by config" and this scenario's triggering depends substantially on full-snapshot cadence configuration/timing rather than exclusively attacker-controlled inputs, likelihood in a default, well-tuned validator is low but the underlying code-level invariant gap is real and not defended against structurally.

### Recommendation
Do not rely on an implicit assumption that ancient-slot age implies full-snapshot coverage. Either (a) explicitly gate ancient-slot selection (`get_sorted_potential_ancient_slots`/`get_oldest_non_ancient_slot`) to never include slots newer than `latest_full_snapshot_slot`, or (b) make `finish_combine_ancient_slots_packed_internal`/`write_packed_storages` handle non-empty `tombstones_to_carry_forward` the same way `shrink_storage` does (write them via `store_tombstones` into the packed storage) instead of asserting they never occur.

### Proof of Concept
Rust integration test plan (extends existing test infra in `accounts-db/src/ancient_append_vecs.rs` tests and `accounts_db/tests/impl.rs`):
1. Build an `AccountsDb` via `create_db_with_storages_and_index`, create an old slot `S` containing a zero-lamport single-ref account (a "tombstone" candidate) plus a normal alive account, and add it as root.
2. Set `latest_full_snapshot_slot` to a value less than `S` (simulating a full snapshot that has not advanced past the ancient boundary), mirroring `test_shrink_collect_carries_forward_existing_tombstones`.
3. Force `S` to be treated as "ancient" independent of the full-snapshot slot, e.g. call `combine_ancient_slots_packed_internal` directly (bypassing/mocking `get_oldest_non_ancient_slot`) with `S` in the slot list, as done in `test_combine_ancient_slots_packed_internal`.
4. Assert: in a debug build, `finish_combine_ancient_slots_packed_internal`'s `debug_assert!` panics; in a release-style equivalent (or by removing the assert to observe end state), assert that `compare_all_accounts` on pre/post account sets shows the zero-lamport tombstone account is missing from the post-pack storage (`new_storage.num_tombstones()` / index membership no longer accounts for it), violating lifecycle-neutrality of ancient packing.
5. Additionally fuzz `(slot, account count/sizes, latest_full_snapshot_slot)` combinations feeding `combine_ancient_slots_packed_for_tests`/`combine_ancient_slots_packed_internal`, asserting no panic and `compare_all_accounts` equality across the operation, to find and confirm the divergence window.

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

**File:** accounts-db/src/accounts_db.rs (L2429-2438)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L2865-2870)
```rust
        let tombstone_refs: Vec<_> = shrink_collect.tombstones_to_carry_forward.iter().collect();
        let tombstone_accounts = [(slot, &tombstone_refs[..])];
        let storable_tombstones = StorableAccountsBySlot::new(slot, &tombstone_accounts, self);
        let (num_tombstones_carried_forward, tombstone_carry_forward_us) = measure_us!(
            self.store_tombstones(shrink_in_progress.new_storage(), storable_tombstones)
        );
```

**File:** accounts-db/src/accounts_db.rs (L3074-3082)
```rust
    /// or which could need to be combined into a new or existing ancient append vec
    /// offset is used to combine newer slots than we normally would. This is designed to be used for testing.
    fn get_sorted_potential_ancient_slots(&self, oldest_non_ancient_slot: Slot) -> Vec<Slot> {
        // Only storages can be combined into ancient append vecs, so the storage map is the
        // source of truth here.
        let mut ancient_slots = self.storage.slots_less_than(oldest_non_ancient_slot);
        ancient_slots.sort_unstable();
        ancient_slots
    }
```

**File:** accounts-db/src/accounts_db.rs (L3084-3099)
```rust
    /// get a sorted list of slots older than an epoch
    /// squash those slots into ancient append vecs
    pub fn shrink_ancient_slots(&self, epoch_schedule: &EpochSchedule) {
        if self.ancient_append_vec_offset.is_none() {
            return;
        }

        let oldest_non_ancient_slot = self.get_oldest_non_ancient_slot(epoch_schedule);
        let can_randomly_shrink = true;
        let (sorted_slots, select_slots_us) =
            measure_us!(self.get_sorted_potential_ancient_slots(oldest_non_ancient_slot));
        self.shrink_ancient_stats
            .select_slots_us
            .fetch_add(select_slots_us, Ordering::Relaxed);
        self.combine_ancient_slots_packed(sorted_slots, can_randomly_shrink);
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L703-720)
```rust
    /// for each slot in 'ancient_slots', collect all accounts in that slot
    /// return the collection of accounts by slot
    fn get_unique_accounts_from_storage_for_combining_ancient_slots<'a>(
        &self,
        ancient_slots: &'a [SlotInfo],
    ) -> Vec<(&'a SlotInfo, GetUniqueAccountsResult)> {
        let mut accounts_to_combine = Vec::with_capacity(ancient_slots.len());

        for info in ancient_slots {
            let unique_accounts = self.get_unique_accounts_from_storage_for_shrink(
                &info.storage,
                &self.shrink_ancient_stats.shrink_stats,
            );
            accounts_to_combine.push((info, unique_accounts));
        }

        accounts_to_combine
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L730-742)
```rust
        let mut dropped_roots = Vec::with_capacity(accounts_to_combine.accounts_to_combine.len());
        for shrink_collect in accounts_to_combine.accounts_to_combine {
            let slot = shrink_collect.slot;

            // Ancient squash only runs on slots far older than the latest full snapshot, where
            // tombstones are purgeable and `shrink_collect` drops them rather than carrying them
            // forward. The squash write path has no tombstone handling, so a non-empty list here
            // would be silently lost; assert the invariant at the point that loss would occur.
            debug_assert!(
                shrink_collect.tombstones_to_carry_forward.is_empty(),
                "ancient squash reached a carry-forward tombstone at slot {slot}",
            );

```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L1539-1626)
```rust
#[test]
fn test_shrink_collect_carries_forward_existing_tombstones() {
    let accounts_db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let slot = 2;
    // Latest full snapshot older than `slot`: tombstones are not yet purgeable.
    accounts_db.set_latest_full_snapshot_slot(slot - 1);

    let alive_pubkey = Pubkey::new_unique();
    let tombstone_pubkey = Pubkey::new_unique();
    let alive_account = AccountSharedData::new(1, 0, &Pubkey::default());
    let zero_lamport_account = AccountSharedData::new(0, 0, &Pubkey::default());

    let (_temp_dirs, paths) = get_temp_accounts_paths(1).unwrap();
    let storage = Arc::new(AccountStorageEntry::new(
        &paths[0],
        slot,
        100,
        DEFAULT_FILE_SIZE,
        accounts_db.accounts_file_provider,
    ));
    // An ordinary alive account, present in the index.
    append_single_account_with_default_hash(
        &storage,
        &alive_pubkey,
        &alive_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    // A zero-lamport account physically in the storage but NOT in the index: i.e. a tombstone
    // carried forward by a prior shrink of an even-older storage.
    append_single_account_with_default_hash(
        &storage,
        &tombstone_pubkey,
        &zero_lamport_account,
        true,
        None,
    );
    accounts_db.storage.insert(Arc::clone(&storage));
    accounts_db.add_root(slot);

    // Record the tombstone account's offset on the storage's tombstone list, as a prior shrink
    // would have.
    let mut tombstone_offset = None;
    storage
        .accounts
        .scan_accounts_without_data(|offset, account| {
            if account.pubkey == &tombstone_pubkey {
                tombstone_offset = Some(offset);
            }
        })
        .unwrap();
    storage.batch_insert_tombstone_offsets([tombstone_offset.unwrap()]);
    assert_eq!(storage.num_zero_lamport_single_ref_accounts(), 1);

    // Newer than the latest full snapshot: the tombstone must be carried forward, not dropped and
    // not mis-routed into the alive set.
    let mut unique_accounts =
        accounts_db.get_unique_accounts_from_storage_for_shrink(&storage, &ShrinkStats::default());
    let shrink_collect = accounts_db.shrink_collect::<AliveAccounts<'_>>(
        &storage,
        &mut unique_accounts,
        &ShrinkStats::default(),
    );
    assert_eq!(shrink_collect.tombstones_to_carry_forward.len(), 1);
    assert!(shrink_collect.tombstones_total_bytes > 0);
    assert_eq!(
        shrink_collect
            .alive_accounts
            .accounts
            .iter()
            .map(|account| *account.pubkey())
            .collect::<Vec<_>>(),
        vec![alive_pubkey],
    );

    // Once the full snapshot advances to `slot`, the tombstone is purgeable and must be dropped
    // rather than carried forward.
    accounts_db.set_latest_full_snapshot_slot(slot);
    let mut unique_accounts =
        accounts_db.get_unique_accounts_from_storage_for_shrink(&storage, &ShrinkStats::default());
    let shrink_collect = accounts_db.shrink_collect::<AliveAccounts<'_>>(
        &storage,
        &mut unique_accounts,
        &ShrinkStats::default(),
    );
    assert!(shrink_collect.tombstones_to_carry_forward.is_empty());
    assert_eq!(shrink_collect.tombstones_total_bytes, 0);
}
```
