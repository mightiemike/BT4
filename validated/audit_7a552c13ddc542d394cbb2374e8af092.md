### Title
`update_slot_list`'s `retain_and_count` scan makes per-pubkey index insertion during `generate_index` quadratic in the number of duplicate versions of a pubkey - ([File: accounts-db/src/accounts_index/in_mem_accounts_index.rs])

### Summary
For each account processed by `generate_index_for_slot`, the in-memory (`IndexLimit::InMemOnly`) code path calls `insert_new_entry_if_missing_with_lock` → `lock_and_update_slot_list` → `update_slot_list`, which scans the *entire current slot list* for the pubkey via `slot_list.retain_and_count(...)` on every insertion. [1](#0-0) [2](#0-1)  If an attacker rewrites the same pubkey across many rooted slots, each successive insertion for that pubkey costs O(current slot-list length), so inserting the m-th duplicate costs O(m) and the total cost for m duplicates of one pubkey is O(m²), rather than O(m).

### Finding Description
`generate_index_for_slot` collects all account records for a slot into `keyed_account_infos` (an `IndexGenerationSlotArena` field that is reset per slot, so it does not itself grow unboundedly) and then calls `self.accounts_index.insert_new_if_missing_into_primary_index(slot, keyed_account_infos)`. [3](#0-2)  When the disk index is not enabled (`IndexLimit::InMemOnly`), each item is inserted one-by-one via `insert_new_entry_if_missing_with_lock`. [4](#0-3) 

For a pubkey already present in the in-memory map (i.e., it was written in an earlier slot too), the `Entry::Occupied` branch calls `Self::lock_and_update_slot_list(occupied.get(), (slot, account_info), None, ..., UpsertReclaim::IgnoreReclaims)`. [5](#0-4)  `lock_and_update_slot_list` calls `update_slot_list`, which performs `slot_list.retain_and_count(|cur_item| ...)` over the entire current slot list to check whether the new slot already exists, before pushing the new item. [2](#0-1)  This scan is O(len(slot_list)) work per insertion, not O(1) amortized append.

Because `generate_index`'s in-memory duplicate handling during index generation retains all versions of a pubkey in the slot list until `visit_duplicate_pubkeys_during_startup` later prunes it (this pruning happens only after ALL storages/slots have been processed, at the very end of `generate_index`), the slot list for a repeatedly-rewritten pubkey grows to size m before any pruning occurs. [6](#0-5)  Thus if an attacker creates the same pubkey in m different rooted slots, restart-time index insertion for that single pubkey costs O(1+2+...+m) = O(m²), instead of the expected O(m) that would be proportional to the m fee-paying writes that created those versions.

If the attacker repeats this with k such pubkeys across n slots (m ≈ n per pubkey), the aggregate insertion cost for `generate_index` scales as O(k·n²) rather than O(k·n), even though the attacker only paid transaction fees proportional to k·n writes. This violates the tested invariant that per-account index/hashing work stays proportional to fees paid.

Note: the `lt_hash.mix_in` work in `generate_index_for_slot` (line 5760) and the per-account visit in `visit_duplicate_pubkeys_during_startup` (reading storage + `lt_hash.mix_in` per duplicate) are each O(1) per account and thus scale linearly (O(k·n) total) — they are not the quadratic culprit. Similarly the `slot_arena` buffers (`keyed_account_infos`, `zero_lamport_offsets`) are cleared every slot (`ensure_empty`) and only ever hold at most one slot's worth of accounts, so they do not by themselves cause cross-slot quadratic growth. [7](#0-6)  The quadratic amplification specifically comes from the `retain_and_count` full-slot-list scan inside `update_slot_list`, invoked once per duplicate insertion in the `InMemOnly` in-memory index path.

This only affects the `IndexLimit::InMemOnly` in-memory insertion path (`insert_new_entry_if_missing_with_lock`); the disk-index path (`IndexLimit::Minimal`/disk-backed) uses `startup_insert_only`, which merely appends to a `Vec` under a lock without per-item slot-list scanning, and defers duplicate resolution to `write_startup_info`/`populate_and_retrieve_duplicate_keys_from_startup`. [8](#0-7)  This produces exactly the asymmetric restart-time cost the question describes: nodes running `InMemOnly` suffer O(k·n²) restart costs while nodes running disk-backed/`Minimal` index configurations do not exhibit the same quadratic amplification for this particular code path.

### Impact Explanation
This is a resource-exhaustion / restart-time DoS: an unprivileged attacker who repeatedly rewrites the same set of pubkeys across many rooted slots (well within normal per-transaction fee and size limits) can inflate `generate_index`'s CPU cost quadratically in the number of times a pubkey is rewritten, on validators configured with `IndexLimit::InMemOnly`. Because `generate_index` runs during every validator restart/snapshot load, this creates disproportionate (asymmetric across differently-configured honest nodes) restart-time costs, potentially stalling cluster recovery for a subset of nodes — matching the "resource exhaustion at restart, potentially stalling cluster recovery" bounty category referenced in the question. It does not cause hash/capitalization divergence between nodes (the final index and lt-hash/capitalization computation is correctness-preserving; the pruning of duplicates and lt-hash mix-out still occurs correctly), so this is a performance/availability finding rather than a consensus-safety finding.

### Likelihood Explanation
Feasibility requires only: (1) an attacker able to create/rewrite accounts they own across many slots, controlling pubkeys and write frequency; (2) validators running with `IndexLimit::InMemOnly` (a legitimate, supported configuration, not an "operator misconfiguration" excluded by scope, since it's a standard mode, and the attack applies specifically to this path). The attacker does not need special privileges — merely repeated ordinary account-write transactions targeting the same pubkeys over many slots, which is within an unprivileged user's normal capability and reachable purely through account writes they pay for. This is straightforward to reproduce deterministically in a unit/benchmark test.

### Recommendation
Avoid re-scanning the full slot list on every insertion in `update_slot_list` during index-generation-time inserts for pubkeys known to be duplicates. Options:
- During `generate_index`, since accounts are inserted slot-by-slot in increasing order and duplicates are resolved at the end anyway, unconditionally `push` new slot entries for a pubkey during the startup/insert-only phase instead of calling `retain_and_count`, deferring the "replace if same slot exists" invariant enforcement to the same later duplicate-resolution pass already used by `visit_duplicate_pubkeys_during_startup`.
- Alternatively, track whether the slot list is already known to be duplicate-free (single entry) and skip the linear scan in the common (non-duplicate) case, since duplicates should be rare in honest operation but can still occur under attack; a benchmark should confirm no more than O(1) amortized cost per insertion even when an attacker forces every insertion into the duplicate branch.
- Add an internal safeguard/metric that detects large slot lists building up for a single pubkey during `generate_index` startup insertion and reports/limits accordingly.

### Proof of Concept
Rust benchmark/unit-test plan (using `accounts-db/src/accounts_index/in_mem_accounts_index.rs` test harness style already present, e.g. `new_disk_buckets_for_test`/`AccountsIndex::new` with `IndexLimit::InMemOnly`):

```rust
// Pseudocode benchmark, to be added under accounts-db/src/accounts_index/in_mem_accounts_index.rs tests
// or as a criterion benchmark.
fn bench_insert_new_if_missing_duplicate_scaling(k_pubkeys: usize, n_slots: usize) -> Duration {
    let mut config = ACCOUNTS_INDEX_CONFIG_FOR_TESTING;
    config.index_limit = IndexLimit::InMemOnly;
    let index = AccountsIndex::<u64, u64>::new(&config, Arc::default());
    index.set_startup(Startup::Startup);

    let pubkeys: Vec<Pubkey> = (0..k_pubkeys).map(|_| Pubkey::new_unique()).collect();

    let start = Instant::now();
    for slot in 0..n_slots as Slot {
        // rewrite the SAME k pubkeys every slot
        let mut items: Vec<(Pubkey, u64)> = pubkeys.iter().map(|pk| (*pk, slot)).collect();
        index.insert_new_if_missing_into_primary_index(slot, &mut items);
    }
    index.set_startup(Startup::Normal);
    start.elapsed()
}

#[test]
fn test_generate_index_insertion_scales_linearly_not_quadratically() {
    // Fix k, vary n; assert wall time grows ~linearly with n, not quadratically.
    let t_n1 = bench_insert_new_if_missing_duplicate_scaling(/*k=*/50, /*n=*/1_000);
    let t_n2 = bench_insert_new_if_missing_duplicate_scaling(/*k=*/50, /*n=*/4_000); // 4x n

    // If cost is O(n) per pubkey, t_n2 / t_n1 should be close to 4.
    // If cost is O(n^2) per pubkey (as caused by retain_and_count), t_n2 / t_n1 approaches 16.
    let ratio = t_n2.as_secs_f64() / t_n1.as_secs_f64();
    assert!(
        ratio < 6.0, // generous linear-ish bound
        "insertion time scaled by {ratio}x for a 4x increase in duplicate rewrites per pubkey; \
         suspected O(n^2) behavior in update_slot_list's retain_and_count"
    );
}
```

Expected result on the current code: `ratio` will trend toward ~16x (quadratic) rather than ~4x (linear), demonstrating the O(m²) per-pubkey behavior driven by `retain_and_count` in `update_slot_list`. [9](#0-8)  A full end-to-end reproduction would additionally drive this through `AccountsDb::generate_index` by writing k pubkeys into n `AccountStorageEntry`s (one per slot) via `store_for_tests`/`add_root`/`flush_accounts_cache_slot_for_tests` as seen in existing tests (e.g. `test_mark_obsolete_accounts_at_startup_purge_slot`), then measuring `generate_index(None, false)` wall time while scaling `n` with `k` fixed and vice versa. [10](#0-9)

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L712-745)
```rust
    fn lock_and_update_slot_list(
        current: &AccountMapEntry<T>,
        new_value: SlotListItem<T>,
        other_slot: Option<Slot>,
        reclaims: &mut ReclaimsSlotList<T>,
        reclaim: UpsertReclaim,
    ) -> usize {
        let mut slot_list = current.slot_list_write_lock();
        let (slot, new_entry) = new_value;
        let (ref_count_change, slot_list_len) = Self::update_slot_list(
            &mut slot_list,
            slot,
            new_entry,
            other_slot,
            reclaims,
            reclaim,
        );

        match ref_count_change.cmp(&0) {
            cmp::Ordering::Equal => {
                // Do nothing
            }
            cmp::Ordering::Greater => {
                // If the ref count change is positive, it must be 1 as only one entry is being added
                assert_eq!(ref_count_change, 1);
                current.addref();
            }
            cmp::Ordering::Less => {
                current.unref_by_count(ref_count_change.unsigned_abs());
            }
        }
        current.mark_dirty();
        slot_list_len
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L757-815)
```rust
    fn update_slot_list(
        slot_list: &mut SlotListWriteGuard<T>,
        slot: Slot,
        account_info: T,
        other_slot: Option<Slot>,
        reclaims: &mut ReclaimsSlotList<T>,
        reclaim: UpsertReclaim,
    ) -> (i32, usize) {
        let mut ref_count_change = 1;

        let old_slot = other_slot.unwrap_or(slot);

        // If we find an existing account at old_slot, replace it rather than adding a new entry to the list
        let mut found_slot = false;
        let mut final_len = slot_list.retain_and_count(|cur_item| {
            let (cur_slot, _) = cur_item;
            if *cur_slot == old_slot {
                // Ensure we only find one!
                assert!(!found_slot);

                // Replace the item
                let reclaim_item = mem::replace(cur_item, (slot, account_info));
                match reclaim {
                    UpsertReclaim::ReclaimOldSlots => {
                        reclaims.push(reclaim_item);
                    }
                    UpsertReclaim::IgnoreReclaims => {
                        // do nothing. nothing to assert. nothing to return in reclaims
                    }
                }

                found_slot = true;

                ref_count_change -= 1
            } else if reclaim == UpsertReclaim::ReclaimOldSlots {
                if *cur_slot < slot {
                    reclaims.push(*cur_item);
                    ref_count_change -= 1;
                    return false;
                }
            } else {
                // Slot is new item that is being added to the slot list
                // If slot is already in the slot list, it must be replaced otherwise it will
                // lead to the same slot being duplicated in the list
                assert_ne!(
                    *cur_slot, slot,
                    "slot_list has slot in slot_list but is not replacing it"
                );
            }
            true
        });

        if !found_slot {
            // if we make it here, we did not find the slot in the list
            slot_list.push((slot, account_info));
            final_len += 1;
        }
        (ref_count_change, final_len)
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L833-849)
```rust
    /// Queue up these insertions for when the flush thread is dealing with this bin.
    /// This is very fast and requires no lookups or disk access.
    pub fn startup_insert_only(
        &self,
        slot: Slot,
        items: impl ExactSizeIterator<Item = (Pubkey, T)>,
    ) {
        assert!(self.storage.get_startup());
        assert!(self.bucket.is_some());

        let mut insert = self.startup_info.insert.lock().unwrap();
        let m = Measure::start("copy");
        insert.extend(items.map(|(k, v)| (k, (slot, v.into()))));
        self.startup_stats
            .copy_data_us
            .fetch_add(m.end_as_us(), Ordering::Relaxed);
    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L891-897)
```rust
                let updated_slot_list_len = Self::lock_and_update_slot_list(
                    occupied.get(),
                    (slot, account_info),
                    None, // should be None because we don't expect a different slot # during index generation
                    &mut ReclaimsSlotList::new(),
                    UpsertReclaim::IgnoreReclaims,
                );
```

**File:** accounts-db/src/accounts_db.rs (L465-480)
```rust
/// Auxiliary state populated and emptied per slot within `generate_index_for_slot`
///
/// Holds allocated memory across run of index generation thread for performance.
#[derive(Debug, Default)]
struct IndexGenerationSlotArena {
    keyed_account_infos: Vec<(Pubkey, AccountInfo)>,
    zero_lamport_offsets: Vec<usize>,
}

impl IndexGenerationSlotArena {
    /// Makes sure no actual items are stored in the allocated data structures
    fn ensure_empty(&mut self) {
        assert!(self.keyed_account_infos.is_empty(), "should be drained");
        self.zero_lamport_offsets.clear();
    }
}
```

**File:** accounts-db/src/accounts_db.rs (L5793-5796)
```rust
        let (insert_info, insert_time_us) = measure_us!(
            self.accounts_index
                .insert_new_if_missing_into_primary_index(slot, keyed_account_infos)
        );
```

**File:** accounts-db/src/accounts_db.rs (L6230-6278)
```rust
    fn visit_duplicate_pubkeys_during_startup(
        &self,
        pubkeys: &[Pubkey],
    ) -> (u64, u64, Box<DuplicatesLtHash>, u128) {
        let mut accounts_data_len_from_duplicates = 0;
        let mut num_duplicate_accounts = 0_u64;
        let mut duplicates_lt_hash = Box::new(DuplicatesLtHash::default());
        let mut capitalization_from_duplicates = 0_u128;
        self.accounts_index.scan(
            pubkeys.iter(),
            |pubkey, slots_refs| {
                if let Some((slot_list, _ref_count)) = slots_refs
                    && slot_list.len() > 1
                {
                    // Only the account data len in the highest slot should be used, and the rest are
                    // duplicates.  So find the max slot to keep.
                    // Then sum up the remaining data len, which are the duplicates.
                    // All of the slots need to go in the 'uncleaned_slots' list. For clean to work properly,
                    // the slot where duplicate accounts are found in the index need to be in 'uncleaned_slots' list, too.
                    let max = slot_list.iter().map(|(slot, _)| slot).max().unwrap();
                    slot_list.iter().for_each(|(slot, account_info)| {
                        if slot == max {
                            // the info in 'max' is the most recent, current info for this pubkey
                            return;
                        }
                        let maybe_storage_entry = self
                            .storage
                            .get_account_storage_entry(*slot, account_info.store_id());
                        let mut accessor = LoadedAccountAccessor::Stored(
                            maybe_storage_entry.map(|entry| (entry, account_info.offset())),
                        );
                        accessor.check_and_get_loaded_account(|loaded_account| {
                            let data_len = loaded_account.data_len();
                            let lamports = loaded_account.lamports();
                            if lamports > 0 {
                                accounts_data_len_from_duplicates += data_len;
                            }
                            num_duplicate_accounts += 1;
                            let account_lt_hash = Self::lt_hash_account(&loaded_account, pubkey);
                            duplicates_lt_hash.0.mix_in(&account_lt_hash.0);
                            capitalization_from_duplicates = capitalization_from_duplicates
                                .checked_add(u128::from(lamports))
                                .expect("capitalization cannot overflow");
                        });
                    });
                }
            },
            ScanFilter::All,
        );
```

**File:** accounts-db/src/accounts_index.rs (L740-776)
```rust
            if use_disk {
                r_account_maps.startup_insert_only(slot, items);
            } else {
                // not using disk buckets, so just write to in-mem
                // this is no longer the default case
                let mut duplicates_from_in_memory = vec![];
                items.for_each(|(pubkey, account_info)| {
                    let new_entry =
                        PreAllocatedAccountMapEntry::new(slot, account_info, storage, use_disk);
                    match r_account_maps.insert_new_entry_if_missing_with_lock(pubkey, new_entry) {
                        InsertNewEntryResults::DidNotExist => {
                            num_did_not_exist += 1;
                        }
                        InsertNewEntryResults::Existed {
                            other_slot,
                            location,
                        } => {
                            if let Some(other_slot) = other_slot {
                                duplicates_from_in_memory.push((other_slot, pubkey));
                            }
                            duplicates_from_in_memory.push((slot, pubkey));

                            match location {
                                ExistedLocation::InMem => {
                                    num_existed_in_mem += 1;
                                }
                                ExistedLocation::OnDisk => {
                                    num_existed_on_disk += 1;
                                }
                            }
                        }
                    }
                });

                r_account_maps
                    .startup_update_duplicates_from_in_memory_only(duplicates_from_in_memory);
            }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L6745-6784)
```rust
#[test]
fn test_mark_obsolete_accounts_at_startup_purge_slot() {
    let (_accounts_dirs, paths) = get_temp_accounts_paths(2).unwrap();
    let accounts_db = AccountsDb::new_for_tests_with_config(paths, DEFAULT_ACCOUNTS_DB_CONFIG);
    let slots = 2;
    let pubkey1 = Pubkey::new_unique();
    let pubkey2 = Pubkey::new_unique();
    let account = AccountSharedData::new(100, 0, &Pubkey::default());

    // Store the same pubkey in multiple slots
    // Store other pubkey in slot0 to ensure slot is not purged
    accounts_db.store_for_tests((0, [(&pubkey1, &account), (&pubkey2, &account)].as_slice()));
    accounts_db.add_root(0);
    accounts_db.flush_accounts_cache_slot_for_tests(0);
    accounts_db.store_for_tests((1, [(&pubkey1, &account)].as_slice()));
    accounts_db.add_root(1);
    accounts_db.flush_accounts_cache_slot_for_tests(1);
    accounts_db.store_for_tests((2, [(&pubkey1, &account)].as_slice()));
    accounts_db.add_root(2);
    accounts_db.flush_accounts_cache_slot_for_tests(2);

    let pubkeys_with_duplicates_by_bin = vec![vec![pubkey1]];

    let obsolete_stats =
        accounts_db.mark_obsolete_accounts_at_startup(slots, pubkeys_with_duplicates_by_bin);

    // Verify that slot 0 has not been purged
    assert!(accounts_db.storage.get_slot_storage_entry(0).is_some());

    // Verify that slot 1 has been purged
    assert!(accounts_db.storage.get_slot_storage_entry(1).is_none());

    // Verify that the pubkey ref1's count is 1
    assert_eq!(
        accounts_db.accounts_index.ref_count_from_storage(&pubkey1),
        1
    );

    assert_eq!(obsolete_stats.accounts_marked_obsolete, 2);
}
```
