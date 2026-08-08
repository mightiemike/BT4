Based on my investigation, this is a subtle but real analog. In `try_load_bank_forks_from_snapshot` (`ledger/src/bank_forks_utils.rs`), `set_latest_full_snapshot_slot()`/`set_last_swept_full_snapshot_slot()` are always initialized to `full_snapshot_archive_info.slot()` — i.e., the slot of the **full snapshot archive**, regardless of whether the bank was actually loaded from that archive or from a local "fastboot" bank-snapshot directory at a *different, higher* slot.

### Title
Fastboot restore initializes `latest_full_snapshot_slot`/`last_swept_full_snapshot_slot` from the wrong (stale) snapshot slot, silently blocking zero-lamport account cleanup - (File: `ledger/src/bank_forks_utils.rs`)

### Summary
`try_load_bank_forks_from_snapshot` can load the bank either from a full+incremental snapshot **archive** or from a locally-persisted "fastboot" bank-snapshot **directory**, which may sit at a slot higher than the latest full snapshot archive on disk [1](#0-0) . After either path, the code unconditionally calls `set_latest_full_snapshot_slot(full_snapshot_archive_info.slot())` and `set_last_swept_full_snapshot_slot(full_snapshot_archive_info.slot())`, using the archive's slot even when the bank was actually booted from the fastboot directory at a different slot [2](#0-1) .

### Finding Description
`latest_full_snapshot_slot` in `AccountsDb` gates whether zero-lamport accounts may be purged by `clean_accounts`/shrink: accounts at or below this slot are eligible for removal, while newer ones must be retained as tombstones so a future incremental snapshot can still propagate the deletion [3](#0-2) [4](#0-3) . This value is explicitly described as something that "must" be set correctly after a successful load so that the background cleaning process purges zero-lamport accounts correctly [5](#0-4) .

However, when the fastboot path is used (`UseSnapshotArchivesAtStartup::Never` or `WhenNewest` selecting a local bank snapshot dir), the bank's actual slot can be strictly greater than `full_snapshot_archive_info.slot()` — this exact skew is called out in the surrounding warning: "Starting up from local state at slot {bank_snapshot.slot}, which is *older* than the latest snapshot archive..." implies the reverse (newer local state) is also expected/common [6](#0-5) . Despite this, `set_latest_full_snapshot_slot`/`set_last_swept_full_snapshot_slot` are seeded from `full_snapshot_archive_info.slot()`, not from the actually-loaded fastboot bank's slot. This under-initializes `latest_full_snapshot_slot` relative to the true state of the running bank.

The direct consequence mirrors the reported bug class ("critical accounting/tracking value not (re)initialized to match the true post-migration/restart state," silently corrupting downstream logic that depends on it): `clean_accounts`'s zero-lamport sweep (`maybe_clean_zero_lamport_single_ref_accounts_after_snapshot`, `sweep_slots_after_snapshot`) treats slots between the stale `latest_full_snapshot_slot` and the real fastboot slot as still gated/newer-than-snapshot, deferring purge of zero-lamport single-ref accounts and tombstone conversion in that range [7](#0-6) . Storage for those slots is retained longer than intended (excess storage/CPU cost from unnecessarily-kept tombstones and slower shrink eligibility), and it changes when accounts are removed relative to a correctly-initialized validator, which can also affect which slots participate in later `generate_index`/lattice-hash duplicate handling during a subsequent snapshot rebuild [8](#0-7) .

### Impact Explanation
This does not directly corrupt capitalization or the accounts hash — `generate_index`/`reconstruct_accountsdb_from_fields` recompute capitalization and the lt-hash independently and are cross-checked against the bank's serialized fields on every full snapshot-archive load [9](#0-8) . The impact here is disproportionate storage retention/CPU cost from delayed cleaning of zero-lamport accounts in the skewed slot range, and inconsistent cleaning behavior between an honest validator that boots via archive vs. one that boots via fastboot with a lagging archive slot on disk, which is within the accepted impact categories (disproportionate storage/CPU cost, honest-node inconsistency in cleanup behavior).

### Likelihood Explanation
This requires operating with `--use-snapshot-archives-at-startup {never,when-newest}` while local fastboot bank-snapshot state is ahead of the on-disk full snapshot archive — a normal, commonly-configured operational scenario (the code's own warning acknowledges the general slot-skew scenario) [6](#0-5) , making this readily reachable without any malicious input.

### Recommendation
When booting via the fastboot bank-snapshot directory, seed `set_latest_full_snapshot_slot`/`set_last_swept_full_snapshot_slot` from the actually-loaded bank's slot (or from the latest full snapshot slot recorded/derivable for that bank), not from `full_snapshot_archive_info.slot()`, so the zero-lamport cleanup gate reflects the real restored state.

### Proof of Concept
1. Run a validator that produces full snapshot archives periodically but also keeps local fastboot bank-snapshot directories at higher slots.
2. Restart with `--use-snapshot-archives-at-startup when-newest` (or `never`) such that `bank_from_snapshot_dir` loads a bank at slot `S_fastboot > S_archive` (the highest full snapshot archive's slot) [10](#0-9) .
3. Observe that `set_latest_full_snapshot_slot`/`set_last_swept_full_snapshot_slot` are called with `S_archive`, not `S_fastboot` [11](#0-10) .
4. Create/observe zero-lamport accounts at slots between `S_archive` and `S_fastboot`; confirm via `sweep_slots_after_snapshot`/`clean_accounts` behavior that they are treated as not-yet-eligible for purge (kept as tombstones) even though the bank has already advanced well past them, unlike a normal boot where `latest_full_snapshot_slot` would equal the bank's own recent full snapshot slot [7](#0-6) .

### Citations

**File:** ledger/src/bank_forks_utils.rs (L178-223)
```rust
    let fastboot_snapshot = match process_options.use_snapshot_archives_at_startup {
        UseSnapshotArchivesAtStartup::Always => None,
        UseSnapshotArchivesAtStartup::Never => {
            let Some(bank_snapshot) =
                snapshot_utils::get_highest_loadable_bank_snapshot(snapshot_config)
            else {
                return Err(BankForksUtilsError::NoBankSnapshotDirectory {
                    flag: use_snapshot_archives_at_startup::cli::LONG_ARG.to_string(),
                    value: UseSnapshotArchivesAtStartup::Never.to_string(),
                });
            };
            // If a newer snapshot archive was downloaded, it is possible that its slot is
            // higher than the local state we will load.  Did the user intend for this?
            if bank_snapshot.slot < latest_snapshot_archive_slot {
                warn!(
                    "Starting up from local state at slot {}, which is *older* than the latest \
                     snapshot archive at slot {}. If this is not desired, change the --{} CLI \
                     option to *not* \"{}\" and restart.",
                    bank_snapshot.slot,
                    latest_snapshot_archive_slot,
                    use_snapshot_archives_at_startup::cli::LONG_ARG,
                    UseSnapshotArchivesAtStartup::Never,
                );
            }
            Some(bank_snapshot)
        }
        UseSnapshotArchivesAtStartup::WhenNewest => {
            snapshot_utils::get_highest_loadable_bank_snapshot(snapshot_config)
                .filter(|bank_snapshot| bank_snapshot.slot >= latest_snapshot_archive_slot)
        }
    };

    let bank = if let Some(fastboot_snapshot) = fastboot_snapshot {
        snapshot_bank_utils::bank_from_snapshot_dir(
            account_paths,
            &fastboot_snapshot,
            genesis_config,
            &process_options.runtime_config,
            process_options.debug_keys.clone(),
            None, // leader_for_tests
            process_options.limit_load_slot_count_from_snapshot,
            process_options.verify_index,
            process_options.accounts_db_config.clone(),
            accounts_update_notifier,
            exit,
        )
```

**File:** ledger/src/bank_forks_utils.rs (L261-277)
```rust
    // We must inform accounts-db of the latest full snapshot slot, which is used by the background
    // processes to handle zero lamport accounts.  Since we've now successfully loaded the bank
    // from snapshots, this is a good time to do that update.
    // Note, this must only be set if we should generate snapshots, so that we correctly
    // handle (i.e. purge) zero lamport accounts.
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

**File:** accounts-db/src/accounts_db.rs (L1717-1759)
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
    }
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

**File:** accounts-db/src/accounts_db.rs (L6218-6284)
```rust
    /// Used during generate_index() to:
    /// 1. get the _duplicate_ accounts from the given pubkeys
    /// 2. get the slots that contained duplicate pubkeys
    /// 3. build up the duplicates lt hash
    ///
    /// Note this should only be used when ALL entries in the accounts index are roots.
    ///
    /// returns tuple of:
    /// - data len sum of all older duplicates
    /// - number of duplicate accounts
    /// - lt hash of duplicates
    /// - capitalization of duplicates
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
        (
            accounts_data_len_from_duplicates as u64,
            num_duplicate_accounts,
            duplicates_lt_hash,
            capitalization_from_duplicates,
        )
```

**File:** runtime/src/snapshot_bank_utils.rs (L224-234)
```rust
    if bank.capitalization() != info.calculated_capitalization {
        // When limit_load_slot_count is set, ignore capitalization mismatches.
        // Because skipped slots may have changed the calculated capitalization,
        // causing a mismatch with the bank's capitalization.
        if limit_load_slot_count_from_snapshot.is_none() {
            return Err(SnapshotError::MismatchedCapitalization(
                bank.capitalization(),
                info.calculated_capitalization,
            ));
        }
    }
```
