## Analysis: Unbounded, uncached obsolete/tombstone offset set rebuilt on every storage scan

### Title
Unbounded obsolete-accounts list causes repeated O(n) cost on every AccountStorageEntry scan - (File: accounts-db/src/account_storage_entry.rs, accounts-db/src/obsolete_accounts.rs)

### Summary
The Moloch report's bug class is "an unbounded, ever-growing collection that a single instruction/tx must fully iterate, so cost scales with the collection's cumulative growth rather than its current useful size." The Agave analog is `AccountStorageEntry::excluded_offsets()`, which rebuilds an `IntSet` of every obsolete-account offset plus every tombstone offset for a storage from scratch, on every call to `scan_accounts`/`scan_accounts_without_data`. The backing store, `ObsoleteAccounts::accounts` in [1](#0-0) , is a plain `Vec` that is only ever appended to via `mark_accounts_obsolete` and is never pruned/compacted except when the whole storage is dropped or replaced by shrink. Because a storage's obsolete list keeps growing as long as the storage stays alive (un-shrunk), and every scan of that storage re-derives the full exclusion set, repeated background scans of a long-lived, un-shrunk storage pay disproportionate, linearly-growing CPU cost.

### Finding Description
`AccountStorageEntry::excluded_offsets()`: [2](#0-1) 

collects `obsolete_accounts_read_lock().filter_obsolete_accounts(None)` (a full linear scan of the `ObsoleteAccounts::accounts` Vec, per `filter_obsolete_accounts`) plus the entire `tombstone_offsets` set, and builds a brand-new `IntSet` every time. This helper is invoked at the top of both `scan_accounts` and `scan_accounts_without_data`: [3](#0-2) 

`ObsoleteAccounts::mark_accounts_obsolete` only ever pushes new items, never removes or compacts existing ones: [4](#0-3) 

An account entry becomes "obsolete" in a storage whenever a later slot rewrites the same pubkey (or a zero-lamport account ages past the last full snapshot), as documented at: [5](#0-4) 

The exclusion set is used repeatedly by real background paths that scan a storage's accounts more than once over its lifetime, e.g. the dirty-store scan in `construct_candidate_clean_keys` (used by every `clean_accounts()` call): [6](#0-5) 

and the exhaustive refcount verifier, which scans every storage: [7](#0-6) 

Unlike the shrink path — which builds its own obsolete-offset `IntSet` once per `shrink_collect` call directly from the raw `AccountsFile` scan and does not go through `excluded_offsets()` [8](#0-7)  — any consumer that goes through `AccountStorageEntry::scan_accounts`/`scan_accounts_without_data` pays the full O(n) rebuild cost on *every* call, where `n` is the storage's cumulative obsolete+tombstone count, not the currently-alive account count. A storage only has its obsolete list reset when it is entirely dropped/replaced, which only happens once shrink criteria (`is_shrinking_productive`/`is_candidate_for_shrink`, alive-ratio thresholds in `select_candidates_by_total_usage`) are met: [9](#0-8) 

Until that happens, a storage can sit around and be re-dirtied/re-scanned multiple times (e.g. across multiple `clean_accounts` cycles as new writes to its slot's pubkeys keep marking more of its accounts obsolete, or via `accounts-db-verify-refcounts`/hash-verification passes), each time re-paying the growing linear cost.

### Impact Explanation
This does not corrupt state, cause a hash mismatch, or leak funds — it is a **disproportionate CPU cost** issue in AccountsDb background processing (clean/shrink/verify paths), matching the accepted impact category "disproportionate storage and CPU cost." An unprivileged user can, through ordinary heavy account-rewrite activity concentrated on accounts originally stored in a single older slot, cause that slot's storage to accumulate a large number of obsolete entries before it becomes shrink-eligible. Every subsequent scan of that storage (clean's dirty-store scan, `--accounts-db-verify-refcounts`, or any future scan path routed through `AccountStorageEntry::scan_accounts*`) re-executes an O(n) `Vec` scan and `IntSet` build, rather than caching/incrementally maintaining the exclusion set. This inflates validator background-service CPU time proportional to historical churn rather than current live-account count, worsening as more transactions are processed against the same storage before shrink catches up.

### Likelihood Explanation
Moderate-low. It requires: (1) a storage that stays un-shrunk for an extended period (achievable because shrink is threshold/ratio-gated, not immediate), (2) many of its original accounts being obsoleted by later writes (normal usage, fully attacker-influenceable by targeting accounts known to live in an old slot), and (3) that storage being scanned multiple times via a path using `AccountStorageEntry::scan_accounts`/`scan_accounts_without_data` (clean's dirty-store routine, or refcount verification). The condition is realistic under sustained validator operation and adversarial account churn but is bounded by the storage's total account capacity and eventually resolved by shrink, so it is a cost amplifier rather than an unbounded/permanent DoS.

### Recommendation
Cache the computed `excluded_offsets()` `IntSet` on `AccountStorageEntry` (invalidated/rebuilt only when `mark_accounts_obsolete`/`batch_insert_tombstone_offsets` add new entries) instead of rebuilding it from scratch on every scan call. Additionally, consider compacting/deduplicating `ObsoleteAccounts::accounts` (e.g., store as an `IntSet<Offset>` alongside slot/data_len side tables) so that lookups and the exclusion-set construction are O(1) amortized rather than O(n) per scan, matching the pattern the Moloch fix used — separating and cheaply tracking removable/zero-balance entries rather than repeatedly reprocessing the whole growing list.

### Proof of Concept
1. Create a storage at slot `S` containing `N` accounts.
2. Over subsequent slots, repeatedly rewrite the same `N` pubkeys (ordinary transfers/updates), each rewrite marking one more entry obsolete in slot `S`'s `AccountStorageEntry::obsolete_accounts` via `mark_accounts_obsolete` (as exercised in [10](#0-9) ), without meeting shrink's productivity/ratio thresholds so slot `S`'s storage is not shrunk.
3. Repeatedly trigger `clean_accounts()` (background service cadence) or run with `--accounts-db-verify-refcounts`; each pass that touches slot `S`'s storage calls `excluded_offsets()` and rebuilds an `IntSet` over the full, ever-growing `obsolete_accounts.accounts` Vec — measurable CPU cost growing linearly with cumulative rewrites rather than with `N` (the storage's live/original size).

### Citations

**File:** accounts-db/src/obsolete_accounts.rs (L13-34)
```rust
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ObsoleteAccounts {
    pub accounts: Vec<ObsoleteAccountItem>,
}

impl ObsoleteAccounts {
    /// Marks the accounts at the given offsets as obsolete
    pub fn mark_accounts_obsolete(
        &mut self,
        newly_obsolete_accounts: impl ExactSizeIterator<Item = (Offset, usize)>,
        slot: Slot,
    ) {
        self.accounts.reserve(newly_obsolete_accounts.len());

        for (offset, data_len) in newly_obsolete_accounts {
            self.accounts.push(ObsoleteAccountItem {
                offset,
                data_len,
                slot,
            });
        }
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L55-63)
```rust
    /// Obsolete Accounts. These are accounts that are still present in the storage
    /// but should be ignored during rebuild. They have been removed
    /// from the accounts index, so they will not be picked up by scan.
    /// Slot is the slot at which the account is no longer needed.
    /// Two scenarios cause an account entry to be marked obsolete
    /// 1. The account was rewritten to a newer slot
    /// 2. The account was set to zero lamports and is older than the last
    ///    full snapshot. In this case, slot is set to the snapshot slot
    pub(crate) obsolete_accounts: RwLock<ObsoleteAccounts>,
```

**File:** accounts-db/src/account_storage_entry.rs (L291-300)
```rust
    /// Collect the offsets that should be excluded from scans
    fn excluded_offsets(&self) -> IntSet<Offset> {
        let mut offsets: IntSet<_> = self
            .obsolete_accounts_read_lock()
            .filter_obsolete_accounts(None)
            .map(|(offset, _)| offset)
            .collect();
        offsets.extend(self.tombstone_offsets_read_lock().iter().copied());
        offsets
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L302-338)
```rust
    /// Iterate over the alive accounts in this storage, excluding obsolete accounts and tombstones.
    /// The return value is the number of values excluded from the scan.
    pub(crate) fn scan_accounts<'a>(
        &'a self,
        reader: &mut impl RequiredLenBufFileRead<'a>,
        mut callback: impl for<'local> FnMut(Offset, StoredAccountInfo<'local>),
    ) -> Result<u64, AccountsFileError> {
        let excluded_offsets = self.excluded_offsets();
        let mut num_excluded = 0;
        self.accounts.scan_accounts(reader, |offset, account| {
            if excluded_offsets.contains(&offset) {
                num_excluded += 1;
                return;
            }
            callback(offset, account);
        })?;
        Ok(num_excluded)
    }

    /// Iterate over the alive accounts in this storage without reading data, excluding obsolete
    /// accounts and tombstones. The return value is the number of values excluded from the scan.
    pub(crate) fn scan_accounts_without_data(
        &self,
        mut callback: impl for<'local> FnMut(Offset, StoredAccountInfoWithoutData<'local>),
    ) -> Result<u64, AccountsFileError> {
        let excluded_offsets = self.excluded_offsets();
        let mut num_excluded = 0;
        self.accounts
            .scan_accounts_without_data(|offset, account| {
                if excluded_offsets.contains(&offset) {
                    num_excluded += 1;
                    return;
                }
                callback(offset, account);
            })?;
        Ok(num_excluded)
    }
```

**File:** accounts-db/src/accounts_db.rs (L1633-1648)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L1761-1796)
```rust
    /// called with cli argument to verify refcounts are correct on all accounts
    /// this is very slow
    /// this function will call Rayon par_iter, so you will want to have thread pool installed if
    /// you want to call this without consuming all the cores on the CPU.
    fn exhaustively_verify_refcounts(&self, max_slot_inclusive: Option<Slot>) {
        info!("exhaustively verifying refcounts as of slot: {max_slot_inclusive:?}");
        let pubkey_refcount = DashMap::<Pubkey, Vec<Slot>>::default();
        let mut storages = self.storage.all_storages();
        // Flush is not running while we verify, so storages are stable. With no slot bound we
        // verify every storage; otherwise we drop storages newer than the bound.
        if let Some(max_slot_inclusive) = max_slot_inclusive {
            storages.retain(|s| s.slot() <= max_slot_inclusive);
        }
        // populate
        storages.par_iter().for_each_init(
            || Box::new(append_vec::new_scan_accounts_reader()),
            |reader, storage| {
                let slot = storage.slot();
                storage
                    .scan_accounts(reader.as_mut(), |_offset, account| {
                        let pk = account.pubkey();
                        match pubkey_refcount.entry(*pk) {
                            dashmap::mapref::entry::Entry::Occupied(mut occupied_entry) => {
                                if !occupied_entry.get().iter().any(|s| s == &slot) {
                                    occupied_entry.get_mut().push(slot);
                                }
                            }
                            dashmap::mapref::entry::Entry::Vacant(vacant_entry) => {
                                vacant_entry.insert(vec![slot]);
                            }
                        }
                    })
                    .expect("must scan accounts storage");
            },
        );
        let total = pubkey_refcount.len();
```

**File:** accounts-db/src/accounts_db.rs (L2554-2566)
```rust
        // Get a set of all obsolete offsets
        // Slot is not needed, as all obsolete accounts can be considered
        // dead for shrink. Zero lamport accounts are not marked obsolete
        let obsolete_offsets: IntSet<_> = store
            .obsolete_accounts_read_lock()
            .filter_obsolete_accounts(None)
            .map(|(offset, _)| offset)
            .collect();

        // Filter all the accounts that are marked obsolete
        let total_starting_accounts = stored_accounts.len();
        stored_accounts.retain(|account| !obsolete_offsets.contains(&account.index_info.offset()));
        let num_obsolete_filtered = total_starting_accounts - stored_accounts.len();
```

**File:** accounts-db/src/accounts_db.rs (L2985-3070)
```rust
    /// Given the input `ShrinkCandidates`, this function sorts the stores by their alive ratio
    /// in increasing order with the most sparse entries in the front. It will then simulate the
    /// shrinking by working on the most sparse entries first and if the overall alive ratio is
    /// achieved, it will stop and return:
    /// first tuple element: the filtered-down candidates and
    /// second duple element: the candidates which
    /// are skipped in this round and might be eligible for the future shrink.
    fn select_candidates_by_total_usage(
        &self,
        shrink_slots: &ShrinkCandidates,
        shrink_ratio: f64,
    ) -> (IntMap<Slot, Arc<AccountStorageEntry>>, ShrinkCandidates) {
        struct StoreUsageInfo {
            slot: Slot,
            alive_ratio: f64,
            alive_bytes_after_shrink: u64,
            store: Arc<AccountStorageEntry>,
        }
        let mut store_usages = Vec::with_capacity(shrink_slots.len());
        let mut total_alive_bytes: u64 = 0;
        let mut total_bytes: u64 = 0;
        for slot in shrink_slots {
            let Some(store) = self.storage.get_slot_storage_entry(*slot) else {
                continue;
            };
            let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&store) as u64;
            total_alive_bytes += alive_bytes_after_shrink;
            let written_bytes = store.written_bytes();
            total_bytes += written_bytes;
            debug_assert!(
                written_bytes > 0,
                "shrink candidate has zero written bytes! slot: {slot} id: {}",
                store.id(),
            );
            let alive_ratio = alive_bytes_after_shrink as f64 / written_bytes as f64;
            store_usages.push(StoreUsageInfo {
                slot: *slot,
                alive_ratio,
                alive_bytes_after_shrink,
                store: store.clone(),
            });
        }
        store_usages.sort_by(|a, b| {
            a.alive_ratio
                .partial_cmp(&b.alive_ratio)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        // Working from the beginning of store_usage which are the most sparse and see when we can stop
        // shrinking while still achieving the overall goals.
        let mut shrink_slots = IntMap::default();
        let mut shrink_slots_next_batch = ShrinkCandidates::default();
        for store_usage in &store_usages {
            let store = &store_usage.store;
            let alive_ratio = (total_alive_bytes as f64) / (total_bytes as f64);
            debug!(
                "alive_ratio: {:?} store_id: {:?}, store_ratio: {:?} requirement: {:?}, \
                 total_bytes: {:?} total_alive_bytes: {:?}",
                alive_ratio,
                store_usage.store.id(),
                store_usage.alive_ratio,
                shrink_ratio,
                total_bytes,
                total_alive_bytes
            );
            if alive_ratio > shrink_ratio {
                // we have reached our goal, stop
                debug!(
                    "Shrinking goal can be achieved at slot {:?}, total_alive_bytes: {:?} \
                     total_bytes: {:?}, alive_ratio: {:}, shrink_ratio: {:?}",
                    store_usage.slot, total_alive_bytes, total_bytes, alive_ratio, shrink_ratio
                );
                if store_usage.alive_ratio < shrink_ratio {
                    shrink_slots_next_batch.insert(store_usage.slot);
                } else {
                    break;
                }
            } else {
                let current_store_size = store.written_bytes();
                let after_shrink_size = store_usage.alive_bytes_after_shrink;
                let bytes_saved = current_store_size.saturating_sub(after_shrink_size);
                total_bytes -= bytes_saved;
                shrink_slots.insert(store_usage.slot, Arc::clone(store));
            }
        }
        (shrink_slots, shrink_slots_next_batch)
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L5472-5546)
```rust
#[test_case(8)]
#[test_case(5)]
#[test_case(0)]
fn test_calculate_storage_count_and_alive_bytes_obsolete_account(
    num_accounts_to_mark_obsolete: usize,
) {
    let accounts = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    accounts.accounts_index.set_startup(Startup::Startup);

    let account_sizes = [1, 5, 10, 50, 100, 500, 1000, 2000];

    // Make sure we have enough accounts to mark obsolete. If this fails, just add more
    // entries to account_sizes
    assert!(account_sizes.len() >= num_accounts_to_mark_obsolete);

    let account_list: Vec<_> = account_sizes
        .into_iter()
        .map(|size| {
            (
                Pubkey::new_unique(),
                AccountSharedData::new(1, size, AccountSharedData::default().owner()),
            )
        })
        .collect();

    let slot0 = 0;
    let storage = accounts.create_store(slot0, 10_000);
    let offsets = storage.accounts.write_accounts(&(slot0, &account_list[..]));

    let offsets = offsets.unwrap().offsets;
    let data_lens = storage.accounts.get_account_data_lens(&offsets);
    let mut offsets: Vec<_> = offsets.into_iter().zip(data_lens).collect();

    // Randomize the accounts that get marked obsolete
    let mut rng = rand::rng();
    offsets.shuffle(&mut rng);

    let (accounts_to_mark_obsolete, accounts_to_keep) =
        offsets.split_at(num_accounts_to_mark_obsolete);

    storage
        .obsolete_accounts
        .write()
        .unwrap()
        .mark_accounts_obsolete(accounts_to_mark_obsolete.iter().cloned(), slot0 + 1);

    let mut reader = crate::append_vec::new_scan_accounts_reader();
    let mut accum = IndexGenerationAccumulator::with_slots_capacity(1);
    accounts.generate_index_for_slot(&mut reader, &mut accum, 0, &storage);
    assert_eq!(
        accum.num_obsolete_accounts_skipped,
        num_accounts_to_mark_obsolete as u64
    );
    assert_eq!(
        accum.storage_info.len(),
        if num_accounts_to_mark_obsolete < account_sizes.len() {
            1
        } else {
            0
        }
    );

    for (slot, value) in accum.storage_info {
        // Sum up the stored size of all non obsolete accounts
        let expected_stored_size: usize = accounts_to_keep
            .iter()
            .map(|(_, data_len)| storage.accounts.calculate_stored_size(*data_len))
            .sum();

        assert_eq!(
            (slot, value.count, value.stored_size),
            (0, accounts_to_keep.len(), expected_stored_size)
        );
    }
    accounts.accounts_index.set_startup(Startup::Normal);
```
