### Title
Unbounded Accumulation of Zero-Lamport Accounts Pending Purge Until Next Full Snapshot Causes Disproportionate Memory/Storage Growth - (File: accounts-db/src/accounts_db.rs)

### Summary
`AccountsDb::filter_zero_lamport_clean_for_incremental_snapshots` defers purging of zero-lamport accounts whose slot is newer than `latest_full_snapshot_slot`, queuing them in the in-memory set `zero_lamport_accounts_to_purge_after_full_snapshot` instead. This queue is drained only when a *new* full snapshot slot advances past the queued entries. Like the DebitaIncentives bug — where locked incentive tokens depend on a future epoch event (activity) that may never happen, with no independent recovery path — these zero-lamport accounts' reclamation depends entirely on the *next full snapshot* being taken, an event that is infrequent (default every 50,000–100,000 slots) and can be delayed indefinitely by config or by degraded snapshot generation, with no other recovery mechanism to bound the set's growth.

### Finding Description
When `clean_accounts` runs, `filter_zero_lamport_clean_for_incremental_snapshots` intentionally protects incremental-snapshot correctness by refusing to purge a zero-lamport account if its slot is above `latest_full_snapshot_slot`. Instead of purging, it inserts the `(slot, pubkey)` pair into `zero_lamport_accounts_to_purge_after_full_snapshot`: [1](#0-0) 

This deferred set is only drained inside `clean_accounts` when `latest_full_snapshot_slot_advanced_since_clean` is observed true, i.e., only after `set_latest_full_snapshot_slot` is called again (which happens when a *new* full snapshot slot is taken): [2](#0-1) 

`zero_lamport_accounts_to_purge_after_full_snapshot` is an unbounded `DashSet<(Slot, Pubkey)>` field on `AccountsDb`: [3](#0-2) 

Between full snapshots, any account driven to zero lamports (an entirely unprivileged, ordinary user action — e.g., withdrawing all lamports from an account) whose zeroing slot is above the current `latest_full_snapshot_slot` is added to this set and cannot be purged, no matter how many `clean_accounts` passes run in between. The only "recovery mechanism" is waiting for the next full snapshot to advance past that slot. The validator itself warns that a large full-snapshot interval "will negatively impact the background cleanup tasks in accounts-db": [4](#0-3) 

This is structurally analogous to the reported bug: a resource (here, in-memory tracking state plus retained on-disk tombstone bytes for the corresponding zero-lamport accounts) is committed based on a future event (the next full snapshot) that is not guaranteed to occur promptly, and there is no independent way to reclaim/purge it earlier.

### Impact Explanation
On a busy validator, every unprivileged user transaction that zeroes out an account's lamports (a very common on-chain pattern) between full-snapshot slots adds an entry to this unbounded set and keeps the corresponding zero-lamport account's storage bytes un-reclaimed as "tombstones" until the next full snapshot slot is taken and clean runs again. Since full snapshot intervals are typically tens of thousands of slots, and the amount of zero-lamport churn scales with cluster activity, this results in disproportionate memory growth (the `DashSet`) and disk storage growth (retained tombstone bytes in AppendVecs) relative to what should be reclaimable garbage, exactly the "disproportionate storage and CPU cost" class of impact.

### Likelihood Explanation
This path is reached on every mainnet-class validator running with snapshot generation enabled (the default), and is triggered purely by unprivileged, ordinary user activity (any account being drained to zero lamports), with no special conditions needed beyond the normal, expected gap between full snapshots. The larger the configured full-snapshot interval (which the code itself warns can be set "excessively large"), the more pronounced the effect.

### Recommendation
Bound the deferred zero-lamport purge set independently of the full-snapshot cadence — for example, by allowing clean to reclaim these entries once it can prove (via incremental-snapshot-safe bookkeeping) that no incremental snapshot will need them, or by adding a size/age-based cap with monitoring/alerting, rather than relying solely on the next full snapshot slot advancing.

### Proof of Concept
Demonstrated by the existing unit test `test_filter_zero_lamport_clean_for_incremental_snapshots`, which shows a zero-lamport account whose slot exceeds `latest_full_snapshot_slot` is retained (not purged) and only removed once `latest_full_snapshot_slot` is advanced far enough: [5](#0-4) 
Repeating this pattern continuously between full snapshots (each new zero-lamport account above the current `latest_full_snapshot_slot`) accumulates entries in `zero_lamport_accounts_to_purge_after_full_snapshot` without bound until the next full snapshot advances.

### Citations

**File:** accounts-db/src/accounts_db.rs (L943-948)
```rust
    /// for incremental snapshot support.
    zero_lamport_accounts_to_purge_after_full_snapshot: DashSet<(Slot, Pubkey)>,

    /// Set by `set_latest_full_snapshot_slot` when the snapshot advances. Read and cleared by
    /// clean
    latest_full_snapshot_slot_advanced_since_clean: AtomicBool,
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

**File:** validator/src/commands/run/execute.rs (L1250-1260)
```rust
    // It is unlikely that a full snapshot interval greater than an epoch is a good idea.
    // Minimally we should warn the user in case this was a mistake.
    if let SnapshotInterval::Slots(full_snapshot_interval_slots) = full_snapshot_archive_interval {
        let full_snapshot_interval_slots = full_snapshot_interval_slots.get();
        if full_snapshot_interval_slots > DEFAULT_SLOTS_PER_EPOCH {
            warn!(
                "The full snapshot interval is excessively large: {full_snapshot_interval_slots}! \
                 This will negatively impact the background cleanup tasks in accounts-db. \
                 Consider a smaller value.",
            );
        }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L5715-5757)
```rust
#[test]
fn test_filter_zero_lamport_clean_for_incremental_snapshots() {
    let slot = 10;

    struct TestParameters {
        latest_full_snapshot_slot: Option<Slot>,
        max_clean_root: Option<Slot>,
        should_contain: bool,
    }

    let do_test = |test_params: TestParameters| {
        let account_info = AccountInfo::new(StorageLocation::AppendVec(42, 128), true);
        let pubkey = solana_pubkey::new_rand();
        let mut key_set = HashSet::default();
        key_set.insert(pubkey);
        let store_count = 0;
        let mut store_counts = HashMap::default();
        store_counts.insert(slot, (store_count, key_set));
        let mut candidates = [HashMap::new()];
        candidates[0].insert(
            pubkey,
            CleaningInfo {
                slot_list: SlotList::from([(slot, account_info)]),
                ref_count: 1,
                ..Default::default()
            },
        );
        let accounts_db =
            AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
        if let Some(latest_full_snapshot_slot) = test_params.latest_full_snapshot_slot {
            accounts_db.set_latest_full_snapshot_slot(latest_full_snapshot_slot);
        }
        accounts_db.filter_zero_lamport_clean_for_incremental_snapshots(
            test_params.max_clean_root,
            &store_counts,
            &mut candidates,
        );

        assert_eq!(
            candidates[0].contains_key(&pubkey),
            test_params.should_contain
        );
    };
```
