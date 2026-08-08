### Title
Unbounded, unprioritized `uncleaned_pubkeys`/`dirty_stores` cleaning queue lets unprivileged dust-account spam inflate `clean_accounts()` cost - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::clean_accounts()` must fully scan every pubkey accumulated in `uncleaned_pubkeys` and `dirty_stores` since the previous clean pass before any of the entries — including ones from legitimate, high-value account updates — can be reclaimed. Because any unprivileged user can cheaply write to arbitrarily many pubkeys (e.g., create/close many zero-data or dust accounts) and every written pubkey is unconditionally appended to this delta set on cache flush, an attacker can inflate the size of the set that clean must process each cycle at negligible cost to themselves, imposing disproportionate CPU cost on the validator and delaying reclamation of storage for everyone else's accounts. This mirrors the reported `batchRelease()` queue-clogging bug class: a shared, unprioritized processing queue fed by cheap entries with no minimum-cost gate and no way to selectively process/skip a subset.

### Finding Description
`uncleaned_pubkeys` is a `DashMap<Slot, Vec<Pubkey>>` that is populated during cache flush with *every* pubkey written in a rooted slot, not just economically significant ones: [1](#0-0) 

This structure, together with `dirty_stores` (a `DashMap<Slot, Arc<AccountStorageEntry>>` populated whenever accounts are removed/rooted), is declared as the single, shared delta set that drives `clean_accounts()`: [2](#0-1) [3](#0-2) 

`construct_candidate_clean_keys()` drains `dirty_stores` and scans every store in it (in full, with no minimum-value or size filter) to build the `candidates` set that `clean_accounts()` will process in that pass: [4](#0-3) 

`clean_accounts()` then does a parallel index scan over the *entire* `candidates` set built above — there is no per-invocation cap, no prioritization by account value/size, and no API to request cleaning of a specific pubkey or subset out of order: [5](#0-4) [6](#0-5) 

Clean is invoked periodically by the accounts background service on a fixed timer, and it processes whatever backlog has accumulated in `uncleaned_pubkeys`/`dirty_stores` since the previous run: [7](#0-6) 

There is no mechanism analogous to a "minimum deposit" (e.g., a minimum account size/lamports threshold before a pubkey is added to the cleaning delta set) and no mechanism analogous to "release a specific queue index" (e.g., an API to force-clean one pubkey without paying for the whole accumulated backlog). Any unprivileged user can cheaply and repeatedly create/rewrite/close many small or zero-data accounts across rooted slots; each such write unconditionally grows `uncleaned_pubkeys` (accounts_db.rs:4559-4575) and, upon account closure, `dirty_stores`. This directly increases the O(n) work `construct_candidate_clean_keys()` and the subsequent parallel scan in `clean_accounts()` must perform on every clean cycle, at a gas/fee cost to the attacker that is far lower than the CPU cost imposed on the validator (dust/zero-data accounts are cheap to write and require no rent-exempt minimum enforcement at the AccountsDb layer itself).

### Impact Explanation
This is a disproportionate CPU-cost issue: an attacker with ordinary transaction fees can force validators to spend materially more CPU time per `clean_accounts()` invocation than the attacker's own transaction cost, and this backlog delays reclamation of storage/index entries touched by legitimate users' accounts, since the whole accumulated set is processed together each cycle with no prioritization. Because `clean_accounts()` runs on the accounts-background thread and gates shrink/purge progress, sustained spam degrades validator performance over time (increased `clean_accounts` and `construct_candidate_clean_keys` timings, larger index/candidate memory footprint) without requiring any privileged role.

### Likelihood Explanation
Likelihood is moderate-to-high for triggering measurable overhead, since creating and closing many cheap accounts (e.g., zero-data System-owned accounts or short-lived token accounts) is standard, low-cost, unprivileged transaction activity, and the accumulation into `uncleaned_pubkeys`/`dirty_stores` is unconditional. However, turning this into a severe, exploit-worthy DoS requires sustained high transaction throughput (bounded by block space/fees), so the practical severity depends on how much backlog can be produced relative to normal clean intervals and how amortized/parallelized `clean_accounts()` is versus an attacker's realistic transaction budget — this trade-off is not fully quantifiable from static code review alone.

### Recommendation
Consider adding safeguards analogous to the report's suggestions: (1) a minimum-cost or minimum-size threshold before a pubkey/account update is added to `uncleaned_pubkeys` (so trivial/dust account churn doesn't inflate the cleaning delta set at negligible cost), and/or (2) prioritized/incremental processing of `uncleaned_pubkeys`/`dirty_stores` so that a large backlog of low-value spam entries cannot delay processing of higher-value or older entries, plus a cap on per-cycle candidate-set growth with carry-over rather than unbounded full-set scans.

### Proof of Concept
Not independently verified with a runnable reproduction in this review; the analysis is based on static code tracing of `flush_write_cache` → `uncleaned_pubkeys` accumulation → `construct_candidate_clean_keys` → `clean_accounts` full-set scan (accounts_db.rs:4559-4575, 1569-1648, 1873-2013) and the fixed-interval invocation in `accounts_background_service.rs:540-555`. A concrete PoC would require running a local validator, submitting a high volume of cheap account-create/close transactions across many rooted slots, and measuring `clean_accounts`/`construct_candidate_clean_keys` datapoint timings before/after to confirm disproportionate CPU scaling — this was not executed as part of this review, so the magnitude of real-world impact is unconfirmed.

### Citations

**File:** accounts-db/src/accounts_db.rs (L920-924)
```rust

    /// Set of unique keys per slot which is used
    /// to drive clean_accounts
    /// Populated when flushing the accounts write cache
    uncleaned_pubkeys: DashMap<Slot, Vec<Pubkey>, BuildNoHashHasher<Slot>>,
```

**File:** accounts-db/src/accounts_db.rs (L937-940)
```rust
    /// Set of stores which are recently rooted or had accounts removed
    /// such that potentially a 0-lamport account update could be present which
    /// means we can remove the account from the index entirely.
    dirty_stores: DashMap<Slot, Arc<AccountStorageEntry>, BuildNoHashHasher<Slot>>,
```

**File:** accounts-db/src/accounts_db.rs (L1569-1648)
```rust
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

        let insert_candidate = |pubkey, is_zero_lamport| {
            let index = self.accounts_index.bin_calculator.bin_from_pubkey(&pubkey);
            let mut candidates_bin = candidates[index].write().unwrap();
            candidates_bin
                .entry(pubkey)
                .or_default()
                .might_contain_zero_lamport_entry |= is_zero_lamport;
        };

        // `min_dirty_slot` (computed above) already holds the oldest dirty slot over this same set.
        timings.oldest_dirty_slot = min_dirty_slot.unwrap_or_default();
        let dirty_store_routine = || {
            let chunk_size = 1.max(dirty_stores_len.saturating_div(rayon::current_num_threads()));
            dirty_stores
                .par_chunks(chunk_size)
                .for_each(|dirty_store_chunk| {
                    dirty_store_chunk.iter().for_each(|(_slot, store)| {
                        store
                            .scan_accounts_without_data(|_offset, account| {
                                let pubkey = *account.pubkey();
                                let is_zero_lamport = account.is_zero_lamport();
                                insert_candidate(pubkey, is_zero_lamport);
                            })
                            .expect("must scan accounts storage");
                    });
                });
        };
```

**File:** accounts-db/src/accounts_db.rs (L1873-1913)
```rust
    pub fn clean_accounts(&self, max_clean_root_inclusive: Option<Slot>, is_startup: bool) {
        if self.exhaustively_verify_refcounts {
            //at startup use all cores to verify refcounts
            if is_startup {
                self.exhaustively_verify_refcounts(max_clean_root_inclusive);
            } else {
                // otherwise, use the background thread pool
                self.thread_pool_background
                    .install(|| self.exhaustively_verify_refcounts(max_clean_root_inclusive));
            }
        }

        let _guard = self.active_stats.activate(ActiveStatItem::Clean);

        let purges_old_accounts_count = AtomicU64::default();

        let mut measure_all = Measure::start("clean_accounts");
        let max_clean_root_inclusive = self.max_clean_root(max_clean_root_inclusive);

        self.report_store_stats();

        let active_guard = self
            .active_stats
            .activate(ActiveStatItem::CleanConstructCandidates);
        let mut measure_construct_candidates = Measure::start("construct_candidates");
        let mut key_timings = CleanKeyTimings::default();
        let (mut candidates, min_dirty_slot) = self.construct_candidate_clean_keys(
            max_clean_root_inclusive,
            is_startup,
            &mut key_timings,
        );
        measure_construct_candidates.stop();
        drop(active_guard);

        let num_candidates = Self::count_pubkeys(&candidates);
        let found_not_zero_accum = AtomicU64::new(0);
        let not_found_on_fork_accum = AtomicU64::new(0);
        let missing_accum = AtomicU64::new(0);
        let useful_accum = AtomicU64::new(0);
        let reclaims = ReclaimsWithNewestSlot::with_capacity(num_candidates as usize);
        let reclaims = Mutex::new(reclaims);
```

**File:** accounts-db/src/accounts_db.rs (L1915-2013)
```rust
        let do_clean_scan = || {
            candidates.par_iter().for_each(|candidates_bin| {
                let mut found_not_zero = 0;
                let mut not_found_on_fork = 0;
                let mut missing = 0;
                let mut useful = 0;
                let mut purges_old_accounts_local = 0;
                let mut candidates_bin = candidates_bin.write().unwrap();
                // Iterate over each HashMap entry to
                // avoid capturing the HashMap in the
                // closure passed to scan thus making
                // conflicting read and write borrows.
                candidates_bin.retain(|candidate_pubkey, candidate_info| {
                    let mut should_collect_reclaims = false;
                    self.accounts_index.scan(
                        iter::once(candidate_pubkey),
                        |_candidate_pubkey, slot_list_and_ref_count| {
                            let mut useless = true;
                            if let Some((slot_list, ref_count)) = slot_list_and_ref_count {
                                // find the highest rooted slot in the slot list
                                let index_in_slot_list = self.accounts_index.latest_slot(
                                    None,
                                    slot_list,
                                    max_clean_root_inclusive,
                                );

                                match index_in_slot_list {
                                    Some(index_in_slot_list) => {
                                        // found info relative to max_clean_root
                                        let (slot, account_info) = &slot_list[index_in_slot_list];
                                        if account_info.is_zero_lamport() {
                                            useless = false;
                                            // The latest one is zero lamports. We may be able to purge it.
                                            // Add all the rooted entries that contain this pubkey.
                                            // We know the highest rooted entry is zero lamports.
                                            candidate_info.slot_list =
                                                self.accounts_index.get_entries_up_to_inclusive(
                                                    slot_list,
                                                    max_clean_root_inclusive,
                                                );
                                            candidate_info.ref_count = ref_count;
                                        } else {
                                            found_not_zero += 1;
                                        }

                                        // If this candidate has multiple rooted slot list entries,
                                        // we should reclaim the older ones.
                                        if slot_list.len() > 1
                                            && *slot
                                                <= max_clean_root_inclusive.unwrap_or(Slot::MAX)
                                        {
                                            should_collect_reclaims = true;
                                            purges_old_accounts_local += 1;
                                            useless = false;
                                        }
                                    }
                                    None => {
                                        // This pubkey is in the index but not in a root slot, so clean
                                        // it up by adding it to the to-be-purged list.
                                        //
                                        // Also, this pubkey must have been touched by some slot since
                                        // it was in the dirty list, so we assume that the slot it was
                                        // touched in must be unrooted.
                                        not_found_on_fork += 1;
                                        should_collect_reclaims = true;
                                        purges_old_accounts_local += 1;
                                        useless = false;
                                    }
                                }
                            } else {
                                missing += 1;
                            }
                            if !useless {
                                useful += 1;
                            }
                        },
                        if candidate_info.might_contain_zero_lamport_entry {
                            ScanFilter::All
                        } else {
                            self.scan_filter_for_shrinking
                        },
                    );
                    if should_collect_reclaims {
                        let reclaims_new =
                            self.collect_reclaims(candidate_pubkey, max_clean_root_inclusive);
                        if !reclaims_new.is_empty() {
                            self.update_candidate_after_reclaims(candidate_info, &reclaims_new);
                            reclaims.lock().unwrap().extend(reclaims_new);
                        }
                    }
                    !candidate_info.slot_list.is_empty()
                });
                found_not_zero_accum.fetch_add(found_not_zero, Ordering::Relaxed);
                not_found_on_fork_accum.fetch_add(not_found_on_fork, Ordering::Relaxed);
                missing_accum.fetch_add(missing, Ordering::Relaxed);
                useful_accum.fetch_add(useful, Ordering::Relaxed);
                purges_old_accounts_count.fetch_add(purges_old_accounts_local, Ordering::Relaxed);
            });
        };
```

**File:** accounts-db/src/accounts_db.rs (L4559-4575)
```rust
        // Add `accounts` to uncleaned_pubkeys since they were written to storage
        // and should be visited by `clean`.
        // If old slots were reclaimed, accounts were already cleaned,
        // but zero lamports need to be visited during clean for full removal.
        if reclaim_method == UpsertReclaim::ReclaimOldSlots {
            self.uncleaned_pubkeys.entry(slot).or_default().extend(
                accounts
                    .into_iter()
                    .filter(|(_pubkey, account)| account.is_zero_lamport())
                    .map(|(pubkey, _account)| pubkey),
            );
        } else {
            self.uncleaned_pubkeys
                .entry(slot)
                .or_default()
                .extend(accounts.into_iter().map(|(pubkey, _account)| *pubkey));
        }
```

**File:** runtime/src/accounts_background_service.rs (L540-555)
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
```
