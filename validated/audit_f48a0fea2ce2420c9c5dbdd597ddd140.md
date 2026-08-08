### Title
Single cheap dead account in a large ancient append vec forces a full rewrite of the entire storage via the `is_candidate_for_shrink`/`SHRINK_INSERT_ANCIENT_THRESHOLD` fallback - (File: `accounts-db/src/accounts_db.rs`)

### Summary
`AccountsDb::is_candidate_for_shrink` treats *any* single dead byte (`alive_bytes < total_bytes`) as sufficient to mark an ancient storage as a shrink candidate when using the default `AccountShrinkThreshold::TotalSpace` policy, bypassing the proportional/ratio-based selection logic (`select_candidates_by_total_usage`, `shrink_ratio = 0.8`) that is normally used to decide whether shrinking is worthwhile. This strict any-dead-byte check is used both in `calc_ancient_slot_info`/`AncientSlotInfos::add` (to populate `best_ancient_slots_to_shrink`) and directly in `shrink_candidate_slots`'s ancient-insertion fallback, letting an attacker who kills one cheap account inside a large, otherwise near-full ancient append vec force the whole storage to be rewritten.

### Finding Description
`is_candidate_for_shrink` (`accounts-db/src/accounts_db.rs:5047-5056`) for the default `TotalSpace` threshold returns `alive_bytes < total_bytes`, i.e. true the moment even one byte of the storage is dead, with no ratio requirement. This check is used in two places that actually decide whether an ancient storage gets rewritten, independent of the aggregate `shrink_ratio` logic:

1. `AncientSlotInfos::add`/`calc_ancient_slot_info` (`accounts-db/src/ancient_append_vecs.rs:94-118`, `582-613`): `is_candidate_for_shrink` directly sets `should_shrink = true`, which routes the slot into `shrink_indexes` and ultimately into `best_slots_to_shrink` (`clear_should_shrink_after_cutoff`, `accounts-db/src/ancient_append_vecs.rs:185-217`) regardless of `percent_of_alive_shrunk_data` (which is `0` for the normal `shrink_ancient_slots` call path, `ancient_append_vecs.rs:362`, yet `best_slots_to_shrink` is still populated for every entry in `shrink_indexes` before the cutoff is applied).
2. `shrink_candidate_slots`'s ancient-insertion fallback (`accounts-db/src/accounts_db.rs:3132-3154`): when there are fewer than `SHRINK_INSERT_ANCIENT_THRESHOLD` (10) normal shrink candidates, it pops a slot from `best_ancient_slots_to_shrink` and unconditionally inserts it into `shrink_slots` if `is_candidate_for_shrink(&store)` is still true — again, any single dead byte qualifies.

Once inserted into `shrink_slots`, `shrink_storage` rewrites the *entire* alive-byte content of that ancient append vec into a new storage, regardless of how large the storage is or how small the dead portion is. This is exactly what the existing repo test `test_shrink_candidate_slots_with_dead_ancient_account` (`accounts-db/src/accounts_db/tests/impl.rs:2832-2905`) demonstrates: overwriting just the smallest of 3 ancient accounts causes the full ancient storage (containing the other two, unrelated, alive accounts) to be rewritten by `shrink_candidate_slots`. In production, an ancient storage can be far larger (ideal size up to `DEFAULT_ANCIENT_STORAGE_IDEAL_SIZE`/`get_ancient_append_vec_capacity()` scaled up by `max_resulting_storages`, and can contain accounts belonging to many unrelated users), so the same single-dead-byte trigger applies to storages many orders of magnitude larger than the 3-account test case.

An unprivileged attacker fully controls the inputs needed: they can create N cheap accounts (small `data_len`, minimal lamports) that get flushed and eventually become ancient/combined into a shared ancient append vec, then overwrite one of them with a zero-lamport/closed account to make it dead. No slot/ancestor, zero-lamport, obsolete-account, or ref-count guard prevents this — those guards only affect what counts as "alive" for `alive_bytes_after_shrink`, not the fact that the ratio/threshold check used to gate expensive ancient rewrites is bypassed by the strict `is_candidate_for_shrink` "any dead byte" semantics.

### Impact Explanation
This is a disproportionate storage/CPU cost issue, matching the accepted category "disproportionate storage and CPU cost." The attacker pays only the cost of one cheap account creation/closure (a couple of small transactions) to force `AccountsDb` background shrink threads to rewrite the full contents of a large ancient append vec, which may contain accounts belonging to many unrelated users unaffected by the attacker's own transactions. Repeated over multiple attacker-controlled dead accounts landing in different ancient storages (which continuously get combined together by `combine_ancient_slots_packed`), this creates a sustained, fee-disproportionate background I/O and CPU burden on validators, contributing to background-thread resource exhaustion as scoped in the question.

### Likelihood Explanation
- Preconditions: default `AccountShrinkThreshold::TotalSpace` config (the default, `DEFAULT_ACCOUNTS_SHRINK_THRESHOLD_OPTION`), a large ancient append vec containing the attacker's cheap account plus other alive accounts, and either (a) fewer than `SHRINK_INSERT_ANCIENT_THRESHOLD` (10) normal shrink candidates present (plausible under moderate-to-low overall shrink activity) for the `shrink_candidate_slots` fallback, or (b) the ancient-combining pass itself picking up the slot via `calc_ancient_slot_info`.
- Feasibility: fully reachable by an unprivileged user; no special permissions needed, matches the exact mechanism validated by the existing unit test `test_shrink_candidate_slots_with_dead_ancient_account`.
- Repeatability: each single dead account created/killed by the attacker can trigger one full rewrite of the ancient storage it lands in; sustained impact requires the attacker to periodically create and kill cheap accounts across ancient slots, which is straightforward and cheap to automate.

### Recommendation
Make `is_candidate_for_shrink` consistent with the ratio-based selection used elsewhere (e.g., require `alive_bytes as f64 / total_bytes as f64 < shrink_ratio` even for the `TotalSpace` variant, or apply a minimum absolute/relative dead-byte threshold) so that a single dead byte in a very large ancient storage does not, by itself, qualify the entire storage for a full rewrite via the `best_ancient_slots_to_shrink` fallback path in `shrink_candidate_slots`. Alternatively, gate the ancient-insertion fallback in `shrink_candidate_slots` (`accounts-db/src/accounts_db.rs:3132-3154`) with the same proportional cutoff logic used by `select_candidates_by_total_usage`/`clear_should_shrink_after_cutoff`, rather than the strict any-dead-byte `is_candidate_for_shrink` check.

### Proof of Concept
Use/extend the existing test `test_shrink_candidate_slots_with_dead_ancient_account` (`accounts-db/src/accounts_db/tests/impl.rs:2832-2905`) with a much larger set of ancient accounts (e.g., 10,000 accounts of realistic size, total storage size in the tens-of-MB range) plus one small dead account, then assert:
1. `db.is_candidate_for_shrink(&storage)` is `true` after killing just the one small account (demonstrating the any-dead-byte trigger).
2. `db.shrink_candidate_slots(&epoch_schedule)` rewrites (measure elapsed CPU/IO, or assert new storage id/written_bytes changed for) the full storage, and compute `bytes_rewritten / bytes_reclaimed` — assert this ratio is unbounded/very large (e.g., > 1000x) as the total number of unrelated alive accounts grows, showing the cost is not sub-linear/bounded relative to the dead bytes reclaimed or fees paid for the attacker's single dead account. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** accounts-db/src/accounts_db.rs (L3132-3154)
```rust
        // If there are too few slots to shrink, add an ancient slot
        // for shrinking.
        if shrink_slots.len() < SHRINK_INSERT_ANCIENT_THRESHOLD {
            let mut ancients = self.best_ancient_slots_to_shrink.write().unwrap();
            while let Some((slot, written_bytes)) = ancients.pop_front() {
                if let Some(store) = self.storage.get_slot_storage_entry(slot)
                    && !shrink_slots.contains(&slot)
                    && written_bytes == store.written_bytes()
                    && self.is_candidate_for_shrink(&store)
                {
                    let ancient_bytes_added_to_shrink =
                        self.alive_bytes_after_shrink(&store) as u64;
                    shrink_slots.insert(slot, store);
                    self.shrink_stats
                        .ancient_bytes_added_to_shrink
                        .fetch_add(ancient_bytes_added_to_shrink, Ordering::Relaxed);
                    self.shrink_stats
                        .ancient_slots_added_to_shrink
                        .fetch_add(1, Ordering::Relaxed);
                    break;
                }
            }
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

**File:** accounts-db/src/ancient_append_vecs.rs (L94-118)
```rust
    fn add(
        &mut self,
        slot: Slot,
        storage: Arc<AccountStorageEntry>,
        alive_bytes_after_shrink: u64,
        can_randomly_shrink: bool,
        ideal_size: NonZeroU64,
        is_high_slot: bool,
        is_candidate_for_shrink: bool,
    ) -> bool {
        let mut was_randomly_shrunk = false;
        if alive_bytes_after_shrink > 0 {
            let written_bytes = storage.written_bytes();
            let should_shrink = if written_bytes > 0 {
                if is_candidate_for_shrink {
                    true
                } else if can_randomly_shrink && rng().random_range(0..10000) == 0 {
                    was_randomly_shrunk = true;
                    true
                } else {
                    false
                }
            } else {
                false
            };
```

**File:** accounts-db/src/ancient_append_vecs.rs (L185-217)
```rust
    /// clear 'should_shrink' for storages after a cutoff to limit how many storages we shrink
    fn clear_should_shrink_after_cutoff(&mut self, tuning: &PackedAncientStorageTuning) {
        let mut bytes_to_shrink_due_to_ratio = Saturating(0);
        // shrink enough slots to write 'percent_of_alive_shrunk_data'% of the total alive data
        // from slots that exceeded the shrink threshold.
        // The goal is to limit overall i/o in this pass while making progress.
        // Simultaneously, we cannot allow the overall budget to be dominated by ancient storages that need to be shrunk.
        // So, we have to limit how much of the total resulting budget can be allocated to re-packing/shrinking ancient storages.
        let threshold_bytes =
            (self.total_alive_bytes_shrink.0 * tuning.percent_of_alive_shrunk_data / 100).min(
                u64::from(tuning.max_resulting_storages)
                    * u64::from(tuning.ideal_storage_size)
                    * tuning.percent_of_alive_shrunk_data
                    / 100,
            );
        // At this point self.shrink_indexes have been sorted by the
        // largest amount of dead bytes first in the corresponding
        // storages.
        self.best_slots_to_shrink = VecDeque::with_capacity(self.shrink_indexes.len());
        for info_index in &self.shrink_indexes {
            let info = &mut self.all_infos[*info_index];
            self.best_slots_to_shrink
                .push_back((info.slot, info.written_bytes));
            if bytes_to_shrink_due_to_ratio.0 >= threshold_bytes {
                // we exceeded the amount to shrink due to alive ratio, so don't shrink this one just due to 'should_shrink'
                // It MAY be shrunk based on total capacity still.
                // Mark it as false for 'should_shrink' so it gets evaluated solely based on # of files.
                info.should_shrink = false;
            } else {
                bytes_to_shrink_due_to_ratio += info.alive_bytes;
            }
        }
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L417-438)
```rust
    fn combine_ancient_slots_packed_internal(
        &self,
        sorted_slots: Vec<Slot>,
        mut tuning: PackedAncientStorageTuning,
        metrics: &mut SquashStatsSub,
    ) {
        self.shrink_ancient_stats
            .slot
            .store(*sorted_slots.first().unwrap_or(&0), Ordering::Relaxed);
        self.shrink_ancient_stats
            .slots_considered
            .fetch_add(sorted_slots.len() as u64, Ordering::Relaxed);
        let mut ancient_slot_infos =
            self.collect_sort_filter_ancient_slots(sorted_slots, &mut tuning);
        self.shrink_ancient_stats
            .ideal_storage_size
            .store(tuning.ideal_storage_size.into(), Ordering::Relaxed);

        std::mem::swap(
            &mut *self.best_ancient_slots_to_shrink.write().unwrap(),
            &mut ancient_slot_infos.best_slots_to_shrink,
        );
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L2824-2905)
```rust
/// This test creates an ancient storage with three alive accounts
/// of various sizes. It then simulates killing one of the
/// accounts in a more recent (non-ancient) slot by overwriting
/// the account that has the smallest data size.  The dead account
/// is expected to be deleted from its ancient storage in the
/// process of shrinking candidate slots.  The capacity of the
/// storage after shrinking is expected to be the sum of alive
/// bytes of the two remaining alive ancient accounts.
#[test]
fn test_shrink_candidate_slots_with_dead_ancient_account() {
    let epoch_schedule = EpochSchedule::default();
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    const ACCOUNT_DATA_SIZES: &[usize] = &[1000, 2000, 150];
    let accounts: Vec<_> = ACCOUNT_DATA_SIZES
        .iter()
        .map(|data_size| {
            (
                Pubkey::new_unique(),
                AccountSharedData::new(1, *data_size, &Pubkey::default()),
            )
        })
        .collect();
    let accounts: Vec<_> = accounts
        .iter()
        .map(|(pubkey, account)| (pubkey, account))
        .collect();
    let starting_ancient_slot = 1;
    db.store_for_tests((starting_ancient_slot, accounts.as_slice()));
    db.add_root_and_flush_write_cache(starting_ancient_slot);
    let storage = db.get_storage_for_slot(starting_ancient_slot).unwrap();
    let ancient_accounts = db.get_unique_accounts_from_storage(&storage);
    // Check that three accounts are indeed present in the combined storage.
    assert_eq!(ancient_accounts.stored_accounts.len(), 3);
    // Find an ancient account with smallest data length.
    // This will be a dead account, overwritten in the current slot.
    let modified_account_pubkey = ancient_accounts
        .stored_accounts
        .iter()
        .min_by(|a, b| a.data_len.cmp(&b.data_len))
        .unwrap()
        .pubkey;
    let modified_account_owner = *AccountSharedData::default().owner();
    let modified_account = AccountSharedData::new(223, 0, &modified_account_owner);
    let ancient_append_vec_offset = db.ancient_append_vec_offset.unwrap().abs();
    let current_slot = epoch_schedule.slots_per_epoch + ancient_append_vec_offset as u64 + 1;
    // Simulate killing of the ancient account by overwriting it in the current slot.
    db.store_for_tests((
        current_slot,
        [(&modified_account_pubkey, &modified_account)].as_slice(),
    ));
    db.add_root_and_flush_write_cache(current_slot);
    // This should remove the dead ancient account from the index.
    db.clean_accounts_for_tests();
    db.shrink_ancient_slots(&epoch_schedule);
    let storage = db.get_storage_for_slot(starting_ancient_slot).unwrap();
    let created_accounts = db.get_unique_accounts_from_storage(&storage);
    // The dead account should still be in the ancient storage,
    // because the storage wouldn't be shrunk with normal alive to
    // capacity ratio.
    assert_eq!(created_accounts.stored_accounts.len(), 3);
    db.shrink_candidate_slots(&epoch_schedule);
    let storage = db.get_storage_for_slot(starting_ancient_slot).unwrap();
    let created_accounts = db.get_unique_accounts_from_storage(&storage);
    // At this point the dead ancient account should be removed
    // and storage capacity shrunk to the sum of alive bytes of
    // accounts it holds.  This is the data lengths of the
    // accounts plus the length of their metadata.
    assert_eq!(
        created_accounts.written_bytes as usize,
        AppendVec::calculate_stored_size(1000) + AppendVec::calculate_stored_size(2000),
    );
    // The above check works only when the AppendVec storage is
    // used. More generally the pubkey of the smallest account
    // shouldn't be present in the shrunk storage, which is
    // validated by the following scan of the storage accounts.
    storage
        .accounts
        .scan_pubkeys(|pubkey| {
            assert_ne!(pubkey, &modified_account_pubkey);
        })
        .expect("must scan accounts storage");
}
```
