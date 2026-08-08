This scenario is already covered by an existing, passing integration test that proves the invariant holds — exactly the proof-of-concept the question asks for.

### Analysis

The hypothesized attack requires `can_purge_zero_lamport_single_ref_after_shrink` to be gated differently "per-pubkey (via differing ref counts)" within the same shrink call. Tracing the code shows this premise is false: `can_purge_zero_lamport_single_ref_after_shrink(slot_to_shrink)` is computed **once per slot**, at the top of `shrink_collect`/`load_accounts_index_for_shrink`, based only on `slot_to_shrink` vs. `latest_full_snapshot_slot()` — not per-pubkey ref counts. [1](#0-0) [2](#0-1) 

Within `load_accounts_index_for_shrink`, every zero-lamport, ref-count-1 pubkey is always pushed to `zero_lamport_single_ref_pubkeys`. If the slot is newer than the latest full snapshot (`!can_purge_zero_lamport_single_ref`), it is *also* pushed to `tombstones` so its bytes are carried into the new storage. [3](#0-2) 

In `shrink_storage`, the tombstones are physically written into the **new** storage (`store_tombstones`) *before* `remove_old_stores_shrink` is called and the **old** storage is dropped. [4](#0-3) 

`remove_old_stores_shrink` then unconditionally removes the zero-lamport-single-ref pubkeys from the index (safe, since a zero-lamport load already returns "not found") and marks the **old** append vec dirty/dropped if `all_are_zero_lamports` is true — but this only discards the already-superseded old storage, not the tombstone bytes, which already live in the new storage by that point. [5](#0-4) 

So the described "mixed gating causes tombstones to be lost when the old append vec is dropped" scenario cannot occur: gating is uniform per slot, and tombstone bytes are copied out to the new storage before the old storage is discarded.

The exact end-to-end scenario requested by the question — shrink converting a zero-lamport single-ref account to a tombstone, followed by an incremental snapshot cycle, with an assertion that the rebuilt node's state (including capitalization/account absence) matches the source bank — already exists and passes as `test_incremental_snapshots_handle_tombstones`: [6](#0-5) 

This test explicitly asserts `deserialized_bank == *bank3` (equivalent to a capitalization/state match) and that `Account1` is not resurrected after rebuilding from full+incremental snapshots. [7](#0-6) 

Additionally, `test_shrink_collect_carries_forward_existing_tombstones` and `test_shrink_converts_zero_lamport_single_ref_account_to_tombstone` verify the tombstone carry-forward and drop-once-purgeable behavior at the unit level, matching the code's design intent. [8](#0-7) [9](#0-8) 

#No vulnerability found for this question.

### Citations

**File:** accounts-db/src/accounts_db.rs (L2412-2413)
```rust
        let can_purge_zero_lamport_single_ref =
            self.can_purge_zero_lamport_single_ref_after_shrink(slot_to_shrink);
```

**File:** accounts-db/src/accounts_db.rs (L2428-2444)
```rust
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
                    } else {
                        all_are_zero_lamports &= stored_account.is_zero_lamport();
                        alive_accounts.add(ref_count, stored_account, slot_list);
                        alive += 1;
                    }
                };
```

**File:** accounts-db/src/accounts_db.rs (L2691-2718)
```rust
    pub(crate) fn remove_old_stores_shrink<'a, T: ShrinkCollectRefs<'a>>(
        &self,
        shrink_collect: &ShrinkCollect<'a, T>,
        stats: &ShrinkStats,
        shrink_in_progress: Option<ShrinkInProgress>,
        shrink_can_be_active: bool,
    ) {
        let mut time = Measure::start("remove_old_stores_shrink");

        // handle the zero lamport alive accounts before calling clean
        // We have to update the index entries for these zero lamport pubkeys before we remove the storage in `mark_dirty_dead_stores`
        // that contained the accounts.
        self.remove_zero_lamport_single_ref_accounts_after_shrink(
            &shrink_collect.zero_lamport_single_ref_pubkeys,
            shrink_collect.slot,
            stats,
        );

        // Purge old, overwritten storage entries
        // This has the side effect of dropping `shrink_in_progress`, which removes the old storage completely. The
        // index has to be correct before we drop the old storage.
        let dead_storages = self.mark_dirty_dead_stores(
            shrink_collect.slot,
            // If all accounts are zero lamports, then we want to mark the entire OLD append vec as dirty.
            shrink_collect.all_are_zero_lamports,
            shrink_in_progress,
            shrink_can_be_active,
        );
```

**File:** accounts-db/src/accounts_db.rs (L2857-2896)
```rust
        let accounts = [(slot, &shrink_collect.alive_accounts.alive_accounts()[..])];
        let storable_accounts = StorableAccountsBySlot::new(slot, &accounts, self);
        stats_sub.store_accounts_stats = self.store_accounts_for_shrink(
            storable_accounts,
            shrink_in_progress.new_storage(),
            UpdateIndexThreadSelection::PoolWithThreshold,
        );

        let tombstone_refs: Vec<_> = shrink_collect.tombstones_to_carry_forward.iter().collect();
        let tombstone_accounts = [(slot, &tombstone_refs[..])];
        let storable_tombstones = StorableAccountsBySlot::new(slot, &tombstone_accounts, self);
        let (num_tombstones_carried_forward, tombstone_carry_forward_us) = measure_us!(
            self.store_tombstones(shrink_in_progress.new_storage(), storable_tombstones)
        );
        stats_sub.tombstone_carry_forward_us = Saturating(tombstone_carry_forward_us);
        stats_sub.num_tombstones_carried_forward =
            Saturating(num_tombstones_carried_forward as u64);

        // Count the bytes actually written to the new storage
        self.shrink_stats.bytes_written.fetch_add(
            shrink_in_progress.new_storage().written_bytes(),
            Ordering::Relaxed,
        );

        rewrite_elapsed.stop();
        stats_sub.rewrite_elapsed_us = Saturating(rewrite_elapsed.as_us());

        // `store_accounts_for_shrink()` above may have purged accounts from some
        // other storage entries (the ones that were just overwritten by this
        // new storage entry). This means some of those stores might have caused
        // this slot to be read to `self.shrink_candidate_slots`, so delete
        // those here
        self.shrink_candidate_slots.lock().unwrap().remove(&slot);

        self.remove_old_stores_shrink(
            &shrink_collect,
            &self.shrink_stats,
            Some(shrink_in_progress),
            false,
        );
```

**File:** accounts-db/src/accounts_db.rs (L5007-5011)
```rust
    /// Can zero lamport single ref accounts in `slot` be purged?
    fn can_purge_zero_lamport_single_ref_after_shrink(&self, slot: Slot) -> bool {
        self.latest_full_snapshot_slot()
            .is_none_or(|latest_full_snapshot_slot| slot <= latest_full_snapshot_slot)
    }
```

**File:** runtime/src/snapshot_bank_utils.rs (L1450-1617)
```rust
    /// Test that the full tombstone path works end to end across snapshots.
    /// Here's the scenario:
    ///
    /// slot 1:
    ///     - fund Account1 (from Account2) to bring it to life
    ///     - take a full snapshot, capturing Account1 with a non-zero balance
    /// slot 2:
    ///     - drain Account1 back to zero lamports (send to Account2)
    ///     - root and flush so slot 2's storage (holding the zero-lamport Account1) is written
    /// slot 3:
    ///     - update Account2 again so its slot-2 version dies, giving slot 2 dead bytes
    ///     - root, flush, and clean so the slot-1 (funded) version of Account1 is removed, leaving
    ///       the zero-lamport account at slot 2 as the lone reference
    ///     - shrink slot 2 into a tombstone: Account1 is removed from the index, its bytes retained
    ///     - take an incremental snapshot, which must carry the tombstone
    ///     - ensure deserializing from full + incremental is equal to this bank
    ///     - ensure Account1 hasn't come back from the dead
    ///
    /// The full snapshot is older than slot 2, so shrink must NOT purge the zero-lamport account; it
    /// has to retain the bytes as a tombstone. The incremental snapshot then carries that tombstone
    /// so that rebuilding from full + incremental overrides the still-funded full-snapshot version
    /// and the account ends up deleted. If the tombstone were dropped during shrink, the rebuild
    /// would resurrect Account1 from the full snapshot and the checks below would fail.
    #[test]
    fn test_incremental_snapshots_handle_tombstones() {
        let key1 = Keypair::new();
        let key2 = Keypair::new();

        let (_tmp_dir, accounts_dir) = create_tmp_accounts_dir_for_tests();
        let bank_snapshots_dir = tempfile::TempDir::new().unwrap();
        let full_snapshot_archives_dir = tempfile::TempDir::new().unwrap();
        let incremental_snapshot_archives_dir = tempfile::TempDir::new().unwrap();
        let snapshot_config = snapshot_config_for_tests(
            &bank_snapshots_dir,
            &full_snapshot_archives_dir,
            &incremental_snapshot_archives_dir,
        );

        let GenesisConfigInfo {
            mut genesis_config,
            mint_keypair,
            ..
        } = create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            1_000_000 * LAMPORTS_PER_SOL,
        );
        // test expects 0 transaction fee
        genesis_config.fee_rate_governor = solana_fee_calculator::FeeRateGovernor::new(0, 0);

        let lamports_to_transfer = 123_456 * LAMPORTS_PER_SOL;
        let (bank0, bank_forks) =
            Bank::new_with_paths_for_tests(&genesis_config, None, vec![accounts_dir.clone()], None)
                .wrap_with_bank_forks_for_tests();
        let leader = *bank0.leader();
        bank0
            .transfer(lamports_to_transfer, &mint_keypair, &key2.pubkey())
            .unwrap();
        bank0.fill_bank_with_ticks_for_tests();

        let full_snapshot_slot = 1;
        let bank1 = Bank::new_from_parent_with_bank_forks(
            bank_forks.as_ref(),
            bank0,
            leader,
            full_snapshot_slot,
        );
        bank1
            .transfer(lamports_to_transfer, &key2, &key1.pubkey())
            .unwrap();
        bank1.fill_bank_with_ticks_for_tests();
        bank1.set_block_id(Some(Hash::default()));
        let full_snapshot_archive_info =
            bank_to_full_snapshot_archive(&snapshot_config, &bank1).unwrap();

        let zeroed_slot = full_snapshot_slot + 1;
        let bank2 =
            Bank::new_from_parent_with_bank_forks(bank_forks.as_ref(), bank1, leader, zeroed_slot);
        bank2
            .transfer(lamports_to_transfer, &key1, &key2.pubkey())
            .unwrap();
        assert_eq!(
            bank2.get_balance(&key1.pubkey()),
            0,
            "Ensure Account1's balance is zero"
        );
        bank2.fill_bank_with_ticks_for_tests();
        bank2.set_block_id(Some(Hash::default()));
        // root and flush so slot 2's storage holding the zero-lamport Account1 is written
        bank2.squash();
        bank2.force_flush_accounts_cache();

        let slot = zeroed_slot + 1;
        let bank3 = Bank::new_from_parent_with_bank_forks(bank_forks.as_ref(), bank2, leader, slot);
        bank3
            .transfer(lamports_to_transfer, &mint_keypair, &key2.pubkey())
            .unwrap();
        bank3.fill_bank_with_ticks_for_tests();
        bank3.set_block_id(Some(Hash::default()));

        // flush and clean so slot 1's funded Account1 is removed, leaving the zero-lamport account
        // at slot 2 as the lone reference
        bank3.squash();
        bank3.force_flush_accounts_cache();
        bank3.clean_accounts();

        let accounts_db = &bank3.rc.accounts.accounts_db;
        // full snapshot is older than slot 2, so shrink keeps the tombstone instead of purging
        accounts_db.set_latest_full_snapshot_slot(full_snapshot_slot);
        assert_eq!(
            accounts_db
                .accounts_index
                .ref_count_from_storage(&key1.pubkey()),
            1,
            "Ensure Account1 is a zero-lamport single-ref in the index before shrink"
        );

        // shrink converts the zero-lamport single-ref into a tombstone
        accounts_db.shrink_all_slots(false, None);
        assert_eq!(
            accounts_db
                .accounts_index
                .ref_count_from_storage(&key1.pubkey()),
            0,
            "Ensure shrink removed the tombstoned account from the index"
        );
        assert!(
            !accounts_db
                .get_storages(zeroed_slot..zeroed_slot + 1)
                .0
                .is_empty(),
            "Ensure the zeroed slot's storage is retained so the tombstone survives for the \
             incremental snapshot"
        );

        let incremental_snapshot_archive_info =
            bank_to_incremental_snapshot_archive(&snapshot_config, &bank3, full_snapshot_slot)
                .unwrap();

        let deserialized_bank = bank_from_snapshot_archives(
            slice::from_ref(&accounts_dir),
            &full_snapshot_archive_info,
            Some(&incremental_snapshot_archive_info),
            &snapshot_config,
            &genesis_config,
            &RuntimeConfig::default(),
            None,
            None, // leader_for_tests
            None,
            false,
            false,
            false,
            ACCOUNTS_DB_CONFIG_FOR_TESTING,
            None,
            Arc::default(),
        )
        .unwrap();
        assert_eq!(
            deserialized_bank, *bank3,
            "Ensure rebuilding from full + incremental (with the tombstone) matches the live bank"
        );
        assert!(
            deserialized_bank
                .get_account_modified_slot(&key1.pubkey())
                .is_none(),
            "Ensure Account1 has not been brought back from the dead"
        );
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L1406-1534)
```rust
/// Ensure that `shrink` converts a not-yet-purgeable zero lamport single ref account into a
/// tombstone in the new storage
#[test]
fn test_shrink_converts_zero_lamport_single_ref_account_to_tombstone() {
    let accounts_db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let slot0 = 0;
    let slot1 = slot0 + 1;
    // latest full snapshot must be older than the slot(s) we plan to shrink,
    // otherwise zero lamport single ref accounts will be purged
    accounts_db.set_latest_full_snapshot_slot(slot0);

    let obsolete_pubkey = Pubkey::new_unique();
    let obsolete_zero_lamport_pubkey = Pubkey::new_unique();
    let zero_lamport_single_ref_pubkey = Pubkey::new_unique();
    let zero_lamport_multi_ref_pubkey = Pubkey::new_unique();
    let alive_pubkey = Pubkey::new_unique();
    let closed_account = AccountSharedData::new(0, 0, &Pubkey::default());
    let open_account = AccountSharedData::new(1, 0, &Pubkey::default());

    let (_temp_dirs, paths) = get_temp_accounts_paths(1).unwrap();
    let storage1 = Arc::new(AccountStorageEntry::new(
        &paths[0],
        slot1,
        10,
        DEFAULT_FILE_SIZE,
        accounts_db.accounts_file_provider,
    ));
    // store an obsolete account; it should not be marked ZLSR
    append_single_account_with_default_hash(
        &storage1,
        &obsolete_pubkey,
        &open_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    // store an obsolete zero lamport account; it should not be marked ZLSR
    append_single_account_with_default_hash(
        &storage1,
        &obsolete_zero_lamport_pubkey,
        &closed_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    // store a zero lamport single ref account; shrink *should* convert it to a tombstone
    append_single_account_with_default_hash(
        &storage1,
        &zero_lamport_single_ref_pubkey,
        &closed_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    // store a zero lamport multi ref account; it should not be marked ZLSR
    append_single_account_with_default_hash(
        &storage1,
        &zero_lamport_multi_ref_pubkey,
        &closed_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    // store an alive account; it should not be marked ZLSR
    append_single_account_with_default_hash(
        &storage1,
        &alive_pubkey,
        &open_account,
        true,
        Some(&accounts_db.accounts_index),
    );
    accounts_db.storage.insert(Arc::clone(&storage1));
    accounts_db.add_root(slot1);

    // we manually created the storage, so nothing got marked
    assert_eq!(storage1.num_zero_lamport_single_ref_accounts(), 0);

    // store the multi ref account again, in slot 2, so it becomes multi ref
    let slot2 = slot1 + 1;
    accounts_db.store_for_tests((
        slot2,
        [(&zero_lamport_multi_ref_pubkey, &closed_account)].as_slice(),
    ));
    accounts_db.add_root(slot2);
    // flush without clean so the ZLMR account isn't marked obsolete in slot 1
    accounts_db.flush_rooted_accounts_cache_without_clean();

    // mark the obsolete accounts as obsolete
    let ancestors = Ancestors::from(vec![slot2]);
    for pubkey in [obsolete_pubkey, obsolete_zero_lamport_pubkey] {
        let account_info = accounts_db
            .accounts_index
            .get_with_and_then(&pubkey, &ancestors, false, |account_info| account_info)
            .unwrap();
        accounts_db.remove_dead_accounts([account_info].iter(), MarkAccountsObsolete::Yes(slot1));
    }

    accounts_db.shrink_slot_forced(slot1);

    let new_storage1 = accounts_db.get_and_assert_single_storage(slot1);

    // ensure ids are different, to indicate shrink ran
    assert_ne!(new_storage1.id(), storage1.id());
    // ensure there are three accounts in the storage now, removing the two obsolete ones: the
    // alive account, the zero-lamport multi-ref account, and the zero-lamport single-ref account
    // carried forward as a tombstone
    assert_eq!(new_storage1.count(), 3);

    // the zero lamport single ref account is dropped from the index now that it is a tombstone
    assert!(
        accounts_db
            .accounts_index
            .get_with_and_then(
                &zero_lamport_single_ref_pubkey,
                &ancestors,
                false,
                |(_slot, _account_info)| (),
            )
            .is_none()
    );

    // it is recorded on the new storage's tombstone list, not the zero-lamport-single-ref list
    assert_eq!(new_storage1.num_tombstones(), 1);
    assert!(
        new_storage1
            .zero_lamport_single_ref_offsets()
            .read()
            .unwrap()
            .is_empty()
    );
    // the combined single-ref + tombstone count still reflects the one removable account
    assert_eq!(new_storage1.num_zero_lamport_single_ref_accounts(), 1);
}
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L1536-1626)
```rust
/// `shrink_collect` must recognize tombstone offsets already recorded on a storage (carried
/// forward by a prior shrink) and route them into `tombstones_to_carry_forward`: rewritten while
/// the slot is newer than the latest full snapshot, and dropped once the snapshot advances past it.
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
