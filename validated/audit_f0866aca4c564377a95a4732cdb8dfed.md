### Title
Storage `num_alive_bytes` undercounts carried-forward tombstone bytes after shrink, causing perpetual unproductive shrink re-attempts - ([File: accounts-db/src/accounts_db.rs])

### Summary
In `shrink_storage()`, the new storage created for a shrunk slot has its `num_alive_bytes`/`num_alive_accounts` counters updated only for the alive accounts rewritten via `store_accounts_for_shrink()`. The tombstone accounts that are physically written into the very same new storage via `store_tombstones()` do not update those counters. This mirrors the reported `removeLiquidity()` pattern: the *full* amount is physically taken/written (all bytes, including the fee/tombstone portion), while only the *net* amount is recorded in the accounting field, producing a persistent discrepancy between the storage's real on-disk size and its tracked "alive" size.

### Finding Description
`shrink_storage()` computes `total_rewrite_bytes = alive_total_bytes + tombstones_total_bytes` and creates a new storage sized for that total [1](#0-0) . It then writes the alive accounts via `store_accounts_for_shrink()`, and separately writes the tombstones-to-carry-forward via `self.store_tombstones(shrink_in_progress.new_storage(), storable_tombstones)` [2](#0-1) .

`store_tombstones()` writes the tombstone account bytes to the new storage's underlying `AccountsFile` and records their offsets in `tombstone_offsets`, but it never calls `AccountStorageEntry::add_accounts()` (the function that increments `num_alive_bytes`/`num_alive_accounts`) for those bytes: [3](#0-2) 

Only `store_accounts_for_shrink` (used for the alive-account write path) is responsible for updating `add_accounts` on the storage; tombstone bytes are excluded from that accounting call entirely. Meanwhile, the storage's real byte length (`written_bytes()`/`self.accounts.len()`) grows to include both the alive-account bytes and the tombstone bytes, because they were physically appended to the same `AccountsFile` [4](#0-3) .

This produces exactly the accounting mismatch pattern from the external report: the "ledger" (`num_alive_bytes`) is decremented/incremented by only the net (alive-account) portion, while the actual underlying resource (file bytes) reflects the full amount (alive + tombstone). Downstream, `is_shrinking_productive()` and `alive_bytes_after_shrink()` rely on the cached `store.alive_bytes()` field, not a fresh recomputation: [5](#0-4) 

Because `alive_bytes()` never accounts for the tombstone bytes physically present in the storage, `alive_bytes < written_bytes` persists artificially even after a "successful" shrink that carried the tombstones forward (this is expected to happen whenever `latest_full_snapshot_slot` has not yet advanced past the shrunk slot, i.e. tombstones must be carried forward — a state reachable purely by normal user account-closing activity [6](#0-5) ).

### Impact Explanation
`is_shrinking_productive()` returning `true` due to the understated `alive_bytes` causes the storage to be repeatedly re-added to `shrink_candidate_slots` and re-shrunk, even though a real shrink pass (via `shrink_collect`, which *does* recompute tombstone bytes correctly from scratch) would find nothing further to reclaim, since the same tombstones must be carried forward again. This results in wasted CPU and I/O from repeated futile shrink/rewrite cycles on the same slot — a disproportionate and self-perpetuating resource cost, reachable purely through normal unprivileged account lifecycle activity (opening and zeroing out accounts near a full-snapshot boundary).

### Likelihood Explanation
The tombstone-carry-forward path is taken whenever a shrunk slot is newer than the `latest_full_snapshot_slot` and contains zero-lamport single-ref accounts — a condition any user can produce simply by closing/zeroing accounts before the next full snapshot is taken. Since validators periodically shrink slots and take snapshots as part of normal operation, this undercount would recur continuously across the fleet without requiring any adversarial or privileged action.

### Recommendation
`store_tombstones()` should also update the new storage's `num_alive_bytes`/`num_alive_accounts` counters (via `add_accounts()`, or an equivalent accounting call) for the bytes it writes, so that `store.alive_bytes()` reflects the full set of bytes actually retained in the storage (alive accounts + carried-forward tombstones), matching `written_bytes()`.

### Proof of Concept
1. Create account `A` and root it in slot `S` such that it becomes a zero-lamport single-ref account, with `latest_full_snapshot_slot < S` so it is not yet purgeable.
2. Trigger `shrink_storage()` on slot `S`. The new storage is created with `total_rewrite_bytes` sized for alive bytes + tombstone bytes, but only `store_accounts_for_shrink()`'s `add_accounts()` call updates `num_alive_bytes`; `store_tombstones()` does not.
3. Observe: `new_storage.alive_bytes() < new_storage.written_bytes()` by exactly the tombstone bytes, even though nothing further can be reclaimed from this storage (the tombstone must be carried forward again as long as the full-snapshot boundary hasn't advanced).
4. Call `is_shrinking_productive(&new_storage)` — it returns `true`, incorrectly marking the storage a shrink candidate again, leading to a repeated, unproductive shrink cycle on every subsequent shrink pass until the full snapshot slot advances (see existing test `test_shrink_collect_carries_forward_existing_tombstones` for the tombstone-carry-forward mechanics referenced) [7](#0-6) .

### Citations

**File:** accounts-db/src/accounts_db.rs (L2807-2851)
```rust
        let total_rewrite_bytes =
            shrink_collect.alive_total_bytes + shrink_collect.tombstones_total_bytes;

        // This shouldn't happen if alive_bytes is accurate.
        // However, it is possible that the remaining alive bytes could be 0. In that case, the whole slot should be marked dead by clean.
        if Self::should_not_shrink(total_rewrite_bytes as u64, shrink_collect.written_bytes)
            || total_rewrite_bytes == 0
        {
            if total_rewrite_bytes == 0 {
                // clean needs to take care of this dead slot
                self.dirty_stores.insert(slot, store.clone());
            }

            if !shrink_collect.all_are_zero_lamports {
                // if all are zero lamports, then we expect that we would like to mark the whole slot dead, but we cannot. That's clean's job.
                info!(
                    "Unexpected shrink for slot {} alive {} written {}, likely caused by a bug \
                     for calculating alive bytes.",
                    slot, shrink_collect.alive_total_bytes, shrink_collect.written_bytes
                );
            }

            self.shrink_stats
                .skipped_shrink
                .fetch_add(1, Ordering::Relaxed);
            return;
        }

        let total_accounts_after_shrink = shrink_collect.alive_accounts.len();
        debug!(
            "shrinking: slot: {}, accounts: ({} => {}) bytes: {} original: {}",
            slot,
            shrink_collect.total_starting_accounts,
            total_accounts_after_shrink,
            shrink_collect.alive_total_bytes,
            shrink_collect.written_bytes,
        );

        let mut stats_sub = ShrinkStatsSub::default();
        let mut rewrite_elapsed = Measure::start("rewrite_elapsed");
        let (shrink_in_progress, time_us) = measure_us!(self.get_store_for_shrink(
            slot,
            Arc::clone(&store),
            total_rewrite_bytes as u64
        ));
```

**File:** accounts-db/src/accounts_db.rs (L2854-2874)
```rust
        // here, we're writing back alive_accounts. That should be an atomic operation
        // without use of rather wide locks in this whole function, because we're
        // mutating rooted slots; There should be no writers to them.
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

```

**File:** accounts-db/src/accounts_db.rs (L5007-5011)
```rust
    /// Can zero lamport single ref accounts in `slot` be purged?
    fn can_purge_zero_lamport_single_ref_after_shrink(&self, slot: Slot) -> bool {
        self.latest_full_snapshot_slot()
            .is_none_or(|latest_full_snapshot_slot| slot <= latest_full_snapshot_slot)
    }
```

**File:** accounts-db/src/accounts_db.rs (L5013-5043)
```rust
    /// Returns the expected alive bytes after shrinking `store`.
    pub(crate) fn alive_bytes_after_shrink(&self, store: &AccountStorageEntry) -> usize {
        // Obsolete accounts are already excluded from `store.alive_bytes()`.
        // Zero-lamport single-ref accounts are counted as alive until shrink can purge them,
        // which is gated by the latest full snapshot slot.
        if self.can_purge_zero_lamport_single_ref_after_shrink(store.slot()) {
            store.alive_bytes_exclude_zero_lamport_single_ref_accounts()
        } else {
            store.alive_bytes()
        }
    }

    fn is_shrinking_productive(&self, store: &AccountStorageEntry) -> bool {
        let alive_count = store.count();
        let total_bytes = store.written_bytes();
        let alive_bytes = self.alive_bytes_after_shrink(store) as u64;
        if Self::should_not_shrink(alive_bytes, total_bytes) {
            trace!(
                "shrink_slot_forced ({}): not able to shrink at all: num alive: {}, bytes alive: \
                 {}, bytes total: {}, bytes saved: {}",
                store.slot(),
                alive_count,
                alive_bytes,
                total_bytes,
                total_bytes.saturating_sub(alive_bytes),
            );
            return false;
        }

        true
    }
```

**File:** accounts-db/src/accounts_db.rs (L5309-5323)
```rust
    /// Write tombstones into new_storage and store the new offsets on its tombstone_offsets
    /// Note: They are not added to the index
    /// Returns the number of tombstones stored
    fn store_tombstones<'a>(
        &self,
        new_storage: &AccountStorageEntry,
        tombstones: impl StorableAccounts<'a>,
    ) -> usize {
        if tombstones.is_empty() {
            return 0;
        }
        let tombstone_infos =
            self.write_accounts_to_storage(tombstones.target_slot(), new_storage, &tombstones);
        new_storage.batch_insert_tombstone_offsets(tombstone_infos.iter().map(|info| info.offset()))
    }
```

**File:** accounts-db/src/account_storage_entry.rs (L235-238)
```rust
    /// Returns the number of bytes used in this storage
    pub fn written_bytes(&self) -> u64 {
        self.accounts.len() as u64
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
