## Analysis

The Skale report describes a class of bug: garbage-collection/removal work that (1) cannot complete in a single call and instead requires several follow-up calls to finish removing an entity, and (2) each call internally does expensive graph-like iteration over related entities (dependencies) before it can finish. The closest analog in the Agave `accounts-db` crate is the `clean_accounts` / `calc_delete_dependencies` machinery, which purges stale/zero-lamport account entries.

### Title
Clean requires multiple background passes and pays disproportionate CPU cost via cross-bin dependency chasing - (File: accounts-db/src/accounts_db.rs)

### Summary
`AccountsDb::clean_accounts` cannot always reclaim a stale account entry in one pass. When a store cannot be deleted because one of its pubkeys still has a live reference elsewhere, `calc_delete_dependencies` propagates that "can't delete" status by walking every pubkey/slot the affected store touches, which may live in a *different* candidate bin, forcing a fresh `HashMap` lookup and re-queue of `pending_stores` for that bin. This dependency chase is repeated once per `clean_accounts()` invocation, and tests explicitly document that a *second* `clean_accounts` call is required to finish removing an account that the first pass could not fully clear.

### Finding Description
`clean_accounts` builds a set of candidate pubkeys per bin [1](#0-0) , and for every store touched by a candidate, computes a `store_counts` map assuming every candidate is deleted [2](#0-1) .

`calc_delete_dependencies` then walks all candidates across all bins to find stores that cannot actually be deleted (because a live account still points to them), and for each such store it must find *every other pubkey* that also lives in that store and re-examine their slot lists, potentially jumping into other bins' candidate maps via `self.accounts_index.bin_calculator.bin_from_pubkey(key)` and `&candidates[candidates_bin_index]` [3](#0-2) . This is effectively an inter-bin dependency graph traversal (`pending_stores` queue), similar in spirit to the Skale `nodeExit` pattern of "iterate over every potential node/schain that could be affected by removing this one node."

Because of this gating logic (a pubkey can only be fully purged once *all* stores containing it are also purgeable, and zero-lamport purges are additionally gated behind `latest_full_snapshot_slot`), a single `clean_accounts` call frequently cannot finish removing an account in one shot. This is explicitly confirmed by the test suite:
- `test_store_clean_after_shrink` comment: "clean to remove pubkey1 from 0, shrink to shrink pubkey1 from 0, then another clean to remove pubkey1 from slot 1" [4](#0-3) 
- `runtime/src/serde_snapshot/tests.rs` comment: "2nd clean needed to clean-up pubkey1" [5](#0-4) 

`clean_accounts` itself is invoked repeatedly on a fixed background cadence (`CLEAN_INTERVAL`) from `AccountsBackgroundService`, not on-demand per removal [6](#0-5) , so a workload that produces many mutually-dependent, hard-to-purge accounts (many pubkeys colocated in the same storage, updated in overlapping but not fully-overlapping slot sets) can inflate both the number of clean passes needed to fully reclaim space and the per-pass cost of `calc_delete_dependencies`'s cross-bin traversal.

### Impact Explanation
This does not cause consensus divergence, stale reads, or panics — accounts are never made incorrectly visible; they are simply not physically reclaimed as promptly as they could be. The impact is purely a resource-cost one: a validator whose workload creates a large "entangled" set of candidate accounts/stores (e.g., many programs writing many accounts into overlapping slots with partial overlap) forces the background `AccountsDb::clean_accounts` pass to do more cross-bin lookup work and to require additional passes to fully shrink/reclaim storage, increasing CPU time and delaying storage reclamation (larger AppendVec footprint for longer). This is a disproportionate-storage/CPU-cost class issue rather than a correctness or safety issue.

### Likelihood Explanation
This is a background maintenance path (`clean_accounts`/`shrink_candidate_slots`) run automatically by every validator, not something requiring a privileged role. Ordinary, unprivileged on-chain transaction activity (rewriting many accounts across many slots such that store-purge eligibility interlocks) is sufficient to increase the number of candidates handled by `calc_delete_dependencies` and the number of clean passes required, so the triggering condition is realistically reachable, though the magnitude of the cost increase (unlike the Skale $50k/gas-limit scenario) is bounded by normal per-slot/per-epoch cleaning cadence rather than a hard single-transaction limit.

### Recommendation
Consider bounding or amortizing `calc_delete_dependencies`'s cross-bin traversal cost (e.g., capping how many additional pubkeys/stores are re-examined per clean pass, or restructuring dependency resolution so that an entangled account only requires visiting each affected store once regardless of how many pubkeys/bins reference it), and add metrics/alerts for clean passes whose `calc_deps_time` or `store_counts_time` grow disproportionately with `retained_keys_count`, so pathological workloads can be detected. [7](#0-6) 

### Proof of Concept
No single-transaction PoC is applicable; this is a resource-amplification pattern in the background cleaner rather than a discrete exploit. The existing regression tests already demonstrate the "multiple passes required" behavior: [8](#0-7) [9](#0-8) 

---

**Caveat:** I could not find a stronger, more concrete analog (e.g., a bug that hits a hard per-transaction/per-slot processing limit like Skale's block gas limit) elsewhere in the codebase's index/clean/shrink/hash paths within the scope restrictions given. If you want, I can dig further into `ancient_append_vecs.rs`'s `combine_ancient_slots_packed` chain (which also does multi-pass slot combination) as an alternative/additional analog.

### Citations

**File:** accounts-db/src/accounts_db.rs (L1259-1357)
```rust
    fn calc_delete_dependencies(
        &self,
        candidates: &[HashMap<Pubkey, CleaningInfo>],
        store_counts: &mut HashMap<Slot, (usize, HashSet<Pubkey>)>,
        min_slot: Option<Slot>,
    ) {
        // Another pass to check if there are some filtered accounts which
        // do not match the criteria of deleting all appendvecs which contain them
        // then increment their storage count.
        let mut already_counted = IntSet::default();
        for (bin_index, bin) in candidates.iter().enumerate() {
            for (pubkey, cleaning_info) in bin.iter() {
                let slot_list = &cleaning_info.slot_list;
                let ref_count = &cleaning_info.ref_count;
                let mut failed_slot = None;
                let all_stores_being_deleted = slot_list.len() as RefCount == *ref_count;
                if all_stores_being_deleted {
                    let mut delete = true;
                    for (slot, _account_info) in slot_list {
                        if let Some(count) = store_counts.get(slot).map(|s| s.0) {
                            debug!("calc_delete_dependencies() slot: {slot}, count len: {count}");
                            if count == 0 {
                                // this store CAN be removed
                                continue;
                            }
                        }
                        // One of the pubkeys in the store has account info to a store whose store count is not going to zero.
                        // If the store cannot be found, that also means store isn't being deleted.
                        failed_slot = Some(*slot);
                        delete = false;
                        break;
                    }
                    if delete {
                        // this pubkey can be deleted from all stores it is in
                        continue;
                    }
                } else {
                    // a pubkey we were planning to remove is not removing all stores that contain the account
                    debug!(
                        "calc_delete_dependencies(), pubkey: {pubkey}, slot list len: {}, ref \
                         count: {ref_count}, slot list: {slot_list:?}",
                        slot_list.len(),
                    );
                }

                // increment store_counts to non-zero for all stores that can not be deleted.
                let mut pending_stores = IntSet::default();
                for (slot, _account_info) in slot_list {
                    if !already_counted.contains(slot) {
                        pending_stores.insert(*slot);
                    }
                }
                while !pending_stores.is_empty() {
                    let slot = pending_stores.iter().next().cloned().unwrap();
                    if Some(slot) == min_slot {
                        if let Some(failed_slot) = failed_slot.take() {
                            info!(
                                "calc_delete_dependencies, oldest slot is not able to be deleted \
                                 because of {pubkey} in slot {failed_slot}"
                            );
                        } else {
                            info!(
                                "calc_delete_dependencies, oldest slot is not able to be deleted \
                                 because of {pubkey}, slot list len: {}, ref count: {ref_count}",
                                slot_list.len()
                            );
                        }
                    }

                    pending_stores.remove(&slot);
                    if !already_counted.insert(slot) {
                        continue;
                    }
                    // the point of all this code: remove the store count for all stores we cannot remove
                    if let Some(store_count) = store_counts.remove(&slot) {
                        // all pubkeys in this store also cannot be removed from all stores they are in
                        let affected_pubkeys = &store_count.1;
                        for key in affected_pubkeys {
                            let candidates_bin_index =
                                self.accounts_index.bin_calculator.bin_from_pubkey(key);
                            let mut update_pending_stores =
                                |bin: &HashMap<Pubkey, CleaningInfo>| {
                                    for (slot, _account_info) in &bin.get(key).unwrap().slot_list {
                                        if !already_counted.contains(slot) {
                                            pending_stores.insert(*slot);
                                        }
                                    }
                                };
                            if candidates_bin_index == bin_index {
                                update_pending_stores(bin);
                            } else {
                                update_pending_stores(&candidates[candidates_bin_index]);
                            }
                        }
                    }
                }
            }
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L1564-1620)
```rust
    /// Construct a list of candidates for cleaning from:
    /// - dirty_stores      -- set of stores which had accounts removed or recently rooted
    /// - uncleaned_pubkeys -- the delta set of updated pubkeys in rooted slots from the last clean
    ///
    /// The function also returns the minimum slot we encountered.
    fn construct_candidate_clean_keys(
        &self,
        max_clean_root_inclusive: Option<Slot>,
        is_startup: bool,
        timings: &mut CleanKeyTimings,
    ) -> CleaningCandidates {
        let mut dirty_store_processing_time = Measure::start("dirty_store_processing");
        let mut dirty_stores = Vec::with_capacity(self.dirty_stores.len());
        // find the oldest dirty slot
        // we'll add logging if that append vec cannot be marked dead
        let mut min_dirty_slot = None::<u64>;
        self.dirty_stores.retain(|slot, store| {
            if max_clean_root_inclusive
                .is_some_and(|max_clean_root_inclusive| *slot > max_clean_root_inclusive)
            {
                true
            } else {
                min_dirty_slot = min_dirty_slot.map(|min| min.min(*slot)).or(Some(*slot));
                dirty_stores.push((*slot, store.clone()));
                false
            }
        });

        // A storage holding only tombstones has no live index entries, so the reclaim path (which
        // marks a slot dead only once its index entries are removed) never cleans it. Purge it
        // directly — but only once it is no longer newer than the latest full snapshot, since until
        // then its tombstones must be retained for an incremental snapshot to propagate the deletion
        // (see `filter_zero_lamport_clean_for_incremental_snapshots`).
        dirty_stores.retain(|(slot, _dirty_store)| {
            if self.can_purge_zero_lamport_single_ref_after_shrink(*slot)
                && self
                    .storage
                    .get_slot_storage_entry(*slot)
                    .is_some_and(|store| store.has_only_tombstones())
            {
                self.purge_dead_slots_from_storage(
                    iter::once(slot),
                    &self.clean_accounts_stats.purge_stats,
                );
                // Purged; drop it from the candidate scan below.
                false
            } else {
                true
            }
        });

        let dirty_stores_len = dirty_stores.len();
        let num_bins = self.accounts_index.bins();
        let candidates: Box<_> =
            std::iter::repeat_with(|| RwLock::new(HashMap::<Pubkey, CleaningInfo>::new()))
                .take(num_bins)
                .collect();
```

**File:** accounts-db/src/accounts_db.rs (L2046-2075)
```rust
        let mut store_counts_time = Measure::start("store_counts");
        let mut store_counts: HashMap<Slot, (usize, HashSet<Pubkey>)> = HashMap::new();
        for candidates_bin in candidates.iter() {
            for (pubkey, cleaning_info) in candidates_bin.iter() {
                let slot_list = &cleaning_info.slot_list;
                debug_assert!(!slot_list.is_empty(), "candidate slot_list can't be empty");
                for (slot, account_info) in slot_list.iter() {
                    if let Some(store_count) = store_counts.get_mut(slot) {
                        store_count.0 -= 1;
                        store_count.1.insert(*pubkey);
                    } else {
                        let mut key_set = HashSet::new();
                        key_set.insert(*pubkey);
                        let count = self
                            .storage
                            .get_account_storage_entry(*slot, account_info.store_id())
                            .map(|store| store.count())
                            .unwrap()
                            - 1;
                        debug!(
                            "store_counts, inserting slot: {}, store id: {}, count: {}",
                            slot,
                            account_info.store_id(),
                            count
                        );
                        store_counts.insert(*slot, (count, key_set));
                    }
                }
            }
        }
```

**File:** accounts-db/src/accounts_db.rs (L2079-2085)
```rust
        let active_guard = self
            .active_stats
            .activate(ActiveStatItem::CleanCalcDeleteDeps);
        let mut calc_deps_time = Measure::start("calc_deps");
        self.calc_delete_dependencies(&candidates, &mut store_counts, min_dirty_slot);
        calc_deps_time.stop();
        drop(active_guard);
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L3382-3421)
```rust
#[test]
fn test_store_clean_after_shrink() {
    let accounts = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let epoch_schedule = EpochSchedule::default();

    let account = AccountSharedData::new(1, 16 * 4096, &Pubkey::default());
    let pubkey1 = solana_pubkey::new_rand();
    accounts.store_for_tests((0, &[(&pubkey1, &account)][..]));

    let pubkey2 = solana_pubkey::new_rand();
    accounts.store_for_tests((0, &[(&pubkey2, &account)][..]));

    let zero_account = AccountSharedData::new(0, 1, &Pubkey::default());
    accounts.store_for_tests((1, &[(&pubkey1, &zero_account)][..]));

    // Add root 0 and flush separately
    accounts.add_root(0);
    accounts.flush_accounts_cache(true, None);

    // clear out the dirty keys
    accounts.clean_accounts_for_tests();

    // flush 1
    accounts.add_root(1);
    accounts.flush_accounts_cache(true, None);

    accounts.print_accounts_stats("pre-clean");

    // clean to remove pubkey1 from 0,
    // shrink to shrink pubkey1 from 0
    // then another clean to remove pubkey1 from slot 1
    accounts.clean_accounts_for_tests();

    accounts.shrink_candidate_slots(&epoch_schedule);

    accounts.clean_accounts_for_tests();

    accounts.print_accounts_stats("post-clean");
    assert_eq!(accounts.accounts_index.ref_count_from_storage(&pubkey1), 0);
}
```

**File:** runtime/src/serde_snapshot/tests.rs (L700-737)
```rust
        ((current_slot - 1)..=current_slot).for_each(|slot| accounts.flush_root_write_cache(slot));
        accounts.clean_accounts_for_tests();
        let accounts = reconstruct_accounts_db_via_serialization(
            &accounts,
            current_slot,
            ACCOUNTS_DB_CONFIG_FOR_TESTING,
        );

        // Set snapshot to zero to avoid cleaning zero-lamport pubkey1
        accounts.set_latest_full_snapshot_slot(0);
        accounts.clean_accounts_for_tests();

        info!("pubkey: {pubkey1}");
        accounts.print_accounts_stats("pre_clean");
        accounts.assert_load_account(current_slot, pubkey1, zero_lamport);
        accounts.assert_load_account(current_slot, pubkey2, old_lamport);
        accounts.assert_load_account(current_slot, dummy_pubkey, dummy_lamport);

        // F: Finally, make Step A cleanable
        current_slot += 1;
        accounts.store_for_tests((current_slot, [(&pubkey2, &account)].as_slice()));
        accounts.add_root(current_slot);

        // Do clean
        accounts.flush_root_write_cache(current_slot);

        // Make zero-lamport pubkey1 cleanable by setting the latest snapshot slot
        accounts.set_latest_full_snapshot_slot(current_slot);
        accounts.clean_accounts_for_tests();

        // 2nd clean needed to clean-up pubkey1
        accounts.clean_accounts_for_tests();

        // Ensure pubkey2 is cleaned from the index finally
        accounts.assert_not_load_account(current_slot, pubkey1);
        accounts.assert_load_account(current_slot, pubkey2, old_lamport);
        accounts.assert_load_account(current_slot, dummy_pubkey, dummy_lamport);
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
