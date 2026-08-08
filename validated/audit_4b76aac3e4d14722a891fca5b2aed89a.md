### Title
Unbounded growth of `zero_lamport_accounts_to_purge_after_full_snapshot` allows unprivileged storage/memory DOS via repeated cheap zero-lamport account churn - ([File: accounts-db/src/accounts_db.rs])

### Summary
Any unprivileged user can cause unbounded growth of an in-memory `DashSet` inside `AccountsDb` by repeatedly creating and draining accounts to zero lamports across many slots, at negligible net cost (transaction fees only, principal lamports are refunded). Each such zero-lamport account that is "too new" relative to the latest full snapshot is added to `zero_lamport_accounts_to_purge_after_full_snapshot` and is only drained once the next full snapshot advances past it — an interval that can span many minutes to hours in practice.

### Finding Description
`filter_zero_lamport_clean_for_incremental_snapshots` inserts a `(slot, pubkey)` tuple into `self.zero_lamport_accounts_to_purge_after_full_snapshot` for every zero-lamport account whose slot is newer than the `latest_full_snapshot_slot`, because purging it now would break incremental-snapshot correctness: [1](#0-0) 

This set is only drained (and its entries handed to `insert_candidate`) when a full snapshot slot advances past the recorded slot, inside `find_pubkeys_to_clean`: [2](#0-1) 

Because Solana's rent model does not require a minimum non-zero final balance — an account can be created and then fully drained back to the payer, yielding a legitimate zero-lamport account (the same mechanism exercised by `test_flush_cache_dont_clean_zero_lamport_account` and `test_clean_purges_zero_lamport_single_ref_at_reclaim`) — an unprivileged user can cheaply mint a large number of unique zero-lamport accounts: [3](#0-2) [4](#0-3) 

Each such round trip (fund → drain to zero) costs the attacker only network transaction fees; the principal lamports return to the attacker's wallet, exactly mirroring the SUI report's "empty/negligible-value deposit repeated many times" DOS pattern, except here the attacker is not even the party losing value — the validator absorbs the growing in-memory bookkeeping cost.

### Impact Explanation
Each cheaply-created zero-lamport account past the last full-snapshot slot adds a permanent (until next full snapshot) entry to an unbounded `DashSet` inside every validator's `AccountsDb`. A high-throughput but low-cost stream of create/drain transactions accumulates entries continuously between full snapshots (which, depending on `--full-snapshot-interval-slots` configuration, can be tens of thousands of slots — multiple hours in real deployments), causing disproportionate memory growth relative to attacker cost and prolonging/inflating the eventual clean/sweep pass once a full snapshot slot advances. This matches the "disproportionate storage and CPU cost" acceptance criterion.

### Likelihood Explanation
The attack requires no privileged role — it is available to any funded account holder able to sign and submit ordinary `system_instruction::transfer`/`create_account` transactions that leave a target account's balance at zero. The only cost is prevailing transaction fees, making sustained account churn economically feasible for an attacker seeking to grief validators, especially validators running with long full-snapshot intervals.

### Recommendation
Bound `zero_lamport_accounts_to_purge_after_full_snapshot` (e.g., cap its size and force early clean/shrink or a full-snapshot trigger when a high-watermark is exceeded), or otherwise decouple the correctness requirement (incremental-snapshot completeness) from unbounded in-memory accumulation, similar to how `BucketMapHolder`/`IndexLimitThreshold` already bound the accounts index's in-memory footprint via high/low watermarks: [5](#0-4) 

### Proof of Concept
1. Configure/observe a validator running with a full-snapshot interval on the order of tens of thousands of slots (a common/default configuration).
2. From an unprivileged account, repeatedly submit transactions in different slots that fund a fresh keypair and then transfer its entire balance back out, leaving it at zero lamports (as exercised by `test_flush_cache_dont_clean_zero_lamport_account`), using a new pubkey each iteration.
3. Trigger `clean_accounts` (occurs periodically in normal validator operation) while the full snapshot slot has not advanced past these newly-zeroed slots.
4. Observe that each new zero-lamport pubkey/slot pair is retained in `zero_lamport_accounts_to_purge_after_full_snapshot` (per `filter_zero_lamport_clean_for_incremental_snapshots`), and that the set only shrinks once a full snapshot slot advances, allowing it to grow proportionally to the number of attacker-submitted churn transactions during the interval.

### Citations

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

**File:** accounts-db/src/accounts_db.rs (L2359-2370)
```rust
                // Do *not* purge zero-lamport accounts if the slot is greater than the last full
                // snapshot slot.  Since we're `retain`ing the accounts-to-purge, I felt creating
                // the `cannot_purge` variable made this easier to understand.  Accounts that do
                // not get purged here are added to a list so they be considered for purging later
                // (i.e. after the next full snapshot).
                assert!(account_info.is_zero_lamport());
                let cannot_purge = *slot > latest_full_snapshot_slot.unwrap();
                if cannot_purge {
                    self.zero_lamport_accounts_to_purge_after_full_snapshot
                        .insert((*slot, *pubkey));
                }
                !cannot_purge
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L3955-3994)
```rust
#[test]
fn test_flush_cache_dont_clean_zero_lamport_account() {
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    // If there is no latest full snapshot, zero lamport accounts can be cleaned and removed
    // immediately. Set latest full snapshot slot to zero to avoid cleaning zero lamport accounts
    db.set_latest_full_snapshot_slot(0);

    let zero_lamport_account_key = Pubkey::new_unique();
    let other_account_key = Pubkey::new_unique();

    let original_lamports = 1;
    let slot0_account =
        AccountSharedData::new(original_lamports, 1, AccountSharedData::default().owner());
    let zero_lamport_account = AccountSharedData::new(0, 0, AccountSharedData::default().owner());

    // Store into slot 0, and then flush the slot to storage
    db.store_for_tests((0, &[(&zero_lamport_account_key, &slot0_account)][..]));
    // Second key keeps other lamport account entry for slot 0 alive,
    // preventing clean of the zero_lamport_account in slot 1.
    db.store_for_tests((0, &[(&other_account_key, &slot0_account)][..]));
    db.add_root(0);
    db.flush_accounts_cache(true, None);
    assert!(db.storage.get_slot_storage_entry(0).is_some());

    // Store into slot 1, a dummy slot that will be dead and purged before flush
    db.store_for_tests((1, &[(&zero_lamport_account_key, &zero_lamport_account)][..]));

    // Store into slot 2, which makes all updates from slot 1 outdated.
    // This means slot 1 is a dead slot. Later, slot 1 will be cleaned/purged
    // before it even reaches storage, but this purge of slot 1 should not affect
    // the refcount of `zero_lamport_account_key` because cached keys do not bump
    // the refcount in the index. This means clean should *not* remove
    // `zero_lamport_account_key` from slot 2
    db.store_for_tests((2, &[(&zero_lamport_account_key, &zero_lamport_account)][..]));
    db.add_root(1);
    db.add_root(2);

    // Flush, then clean. Should not need another root to initiate the cleaning
    // because `accounts_index.uncleaned_roots` should be correct
    db.flush_accounts_cache(true, None);
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L4861-4909)
```rust
fn test_clean_purges_zero_lamport_single_ref_at_reclaim() {
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let account_key1 = Pubkey::new_unique();
    let account_key2 = Pubkey::new_unique();
    let account_key3 = Pubkey::new_unique();
    let account1 = AccountSharedData::new(1, 0, AccountSharedData::default().owner());
    let account0 = AccountSharedData::new(0, 0, AccountSharedData::default().owner());

    // Store into slot 0
    db.store_for_tests((0, [(&account_key1, &account1)].as_slice()));
    db.store_for_tests((0, [(&account_key2, &account1)].as_slice()));
    db.store_for_tests((0, [(&account_key3, &account1)].as_slice()));
    db.add_root_and_flush_write_cache(0);

    // Make account_key1 and account_key3 in slot 0 outdated by updating in rooted slots 1
    // and 3 with zero lamport accounts
    db.store_for_tests((1, &[(&account_key1, &account0)][..]));
    db.add_root(1);
    db.store_for_tests((3, &[(&account_key3, &account0)][..]));
    db.add_root(3);
    // Flushes all roots without clean
    db.flush_rooted_accounts_cache_without_clean();

    // Gate zero-lamport purging above slot 1: account_key1's zero-lamport update is
    // covered by the full snapshot, account_key3's is not.
    db.set_latest_full_snapshot_slot(1);

    // Clean reclaims the outdated slot 0 entries, unreffing them at reclaim. That leaves
    // each zero-lamport update as its account's only ref.
    db.clean_accounts(Some(3), false);

    // account_key1's purge is not gated, so the same clean pass purges the account: the
    // pubkey is removed from the index and slot 1's storage, left with no live accounts,
    // is dropped.
    assert_eq!(db.accounts_index.ref_count_from_storage(&account_key1), 0);
    assert!(!db.accounts_index.contains(&account_key1));
    assert_no_storages_at_slot(&db, 1);

    // account_key3's purge is gated behind the full snapshot, so it is instead marked
    // zero-lamport single-ref in slot 3's storage, which now holds only such accounts and
    // is queued for clean via dirty_stores rather than shrink.
    assert_eq!(db.accounts_index.ref_count_from_storage(&account_key3), 1);
    assert_eq!(
        db.get_and_assert_single_storage(3)
            .num_zero_lamport_single_ref_accounts(),
        1
    );
    assert!(db.dirty_stores.contains_key(&3));
    assert!(!db.shrink_candidate_slots.lock().unwrap().contains(&3));
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L28-49)
```rust
/// The number of entries below an in-mem index bin's usable capacity at which to begin evicting.
///
/// This number should be *at least* the worst case rate that entries are added to the in-mem
/// index per bin.  This ensures we start evicting early enough so that we do not exceed the
/// configured index threshold limit.
///
/// At the same time, we want this value to be as small as possible.  The smaller this value, the
/// higher the utilization of the in-mem index bins.
///
/// This value is used to compute the high watermark.
pub const DEFAULT_NUM_ENTRIES_OVERHEAD: usize = 5_000;

/// The number of entries to evict, once we've hit the high watermark.
///
/// We want this number to be small, similar to `NUM_ENTRIES_OVERHEAD`, to keep utilization high.
/// It also must be large enough to ensure once an eviction is triggered that scanning + flushing +
/// evicting completes before the high watermark is crossed again.
/// We also want to avoid/ammortize scanning the bins for flush/evict, so a larger number helps
/// with that goal.
///
/// This value is used to compute the low watermark.
pub const DEFAULT_NUM_ENTRIES_TO_EVICT: usize = 10_000;
```
