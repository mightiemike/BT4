## Analysis

The PoolTogether report describes a pattern where an unprivileged participant can selectively cause a *revert* on a specific sub-case of a batched, protocol-level decision (canary-tier claims) so that the aggregate state-transition ("tier expansion") never proceeds — at no cost to the attacker, and with no single victim, but with the effect of letting one user's behavior control a system-wide outcome that should be impartial.

The closest reachable analog in this codebase is in the ancient-append-vec packing logic in `accounts-db`, which is one of the allowed areas (accounts storage/index, cache flush/shrink/clean/purge).

### Title
Attacker-controlled multi-ref accounts can abort the entire ancient-slot packing batch, indefinitely blocking `shrink_ancient_slots` and causing unbounded ancient storage growth - (File: `accounts-db/src/ancient_append_vecs.rs`)

### Summary
`combine_ancient_slots_packed_internal` computes a batch of "target slots" that can absorb accounts from many ancient storages, then calls `many_ref_accounts_can_be_moved` to check that accounts with `ref_count > 1` (whose current index entry is the newest alive instance) can be legally relocated into that batch. If this check fails for even a small subset of accounts, the function **returns early and discards the entire batch**, performing no packing/shrinking for any of the (potentially many) otherwise-poolable ancient slots in that call. [1](#0-0) 

### Finding Description
`many_ref_accounts_can_be_moved` requires that every multi-ref "newest alive" account's current slot be `<=` a `highest_slot` derived from how many ideal-sized target storages are needed to hold those multi-ref accounts: [2](#0-1) 

If `target_slots_sorted` doesn't contain a slot high enough to satisfy this (e.g., because the multi-ref account was touched again very recently, in a slot higher than any currently-ancient target slot available for repacking), the function returns `false`, and the caller aborts the *entire* combine operation for the whole `sorted_slots` batch: [3](#0-2) 

Because `ref_count > 1` accounts must remain at-or-above their current slot to preserve the "highest slot = most recent version" invariant (as documented in `calc_accounts_to_combine`), any unprivileged user can create such multi-ref accounts simply through ordinary write activity to the same pubkey across many slots: [4](#0-3) 

An attacker (or several coordinated ordinary accounts) who periodically re-writes a small set of pubkeys can keep the "newest alive" reference for those pubkeys pinned to a recent slot on every combining pass. As long as this happens more frequently than `shrink_ancient_slots`/`combine_ancient_slots_packed` completes a batch, `many_ref_accounts_can_be_moved` will repeatedly return `false`, and *the whole batch* — potentially containing hundreds of unrelated, perfectly packable single-ref ancient slots — is discarded (`return;` at line 481) rather than partially processed.

### Impact Explanation
This does not cause fund loss or a consensus divergence by itself, but it lets an unprivileged actor unilaterally suppress a maintenance process (ancient slot combining) that the whole cluster relies on to keep the number of ancient storages/roots bounded (`DEFAULT_MAX_ANCIENT_STORAGES`, `max_ancient_slots`). Persistently blocking this batch operation causes:
- Unbounded accumulation of small/ancient append-vec storages (extra file handles, extra disk usage),
- Repeated wasted CPU/IO work every call to `shrink_ancient_slots`/`combine_ancient_slots_packed` (`collect_sort_filter_ancient_slots`, `get_unique_accounts_from_storage_for_combining_ancient_slots`, `calc_accounts_to_combine` all still run and are thrown away),
- This is deterministic and reproducible by every honest validator identically, i.e., a disproportionate, sustained storage and CPU cost imposed cluster-wide by a single user's account-access pattern, analogous to the report's "user has control over the [batched] expansion process."

### Likelihood Explanation
Requires no special role — any transaction sender can repeatedly write to the same pubkey(s) across slots to keep creating fresh "newest alive" multi-ref entries. Whether this can be sustained in practice depends on cadence of `shrink_ancient_slots` calls versus attacker's slot cadence, and on how quickly `target_slots_sorted` accumulates enough eligible slots to absorb the multi-ref account regardless — so likelihood is moderate and needs a PoC/simulation to confirm it can be sustained indefinitely rather than merely delaying a batch by one cycle.

### Recommendation
Rather than aborting the whole batch when `many_ref_accounts_can_be_moved` fails, the code should fall back to packing all `target_slots_sorted` entries that don't depend on the unmovable multi-ref accounts (i.e., only exclude/retry the specific slots that contain the problematic multi-ref accounts, similar to how `calc_accounts_to_combine`'s `IncludeManyRefSlots::Skip` already removes individual unpackable slots) instead of discarding all already-computed packable work with a single early `return`.

### Proof of Concept
Not fully provable without a running validator/harness; would need a test extending `test_calc_accounts_to_combine_many_refs`/`test_combine_ancient_slots_packed` style tests in `accounts-db/src/ancient_append_vecs.rs` to show that repeatedly re-writing a pubkey across ancient-eligible slots causes `many_ref_accounts_can_be_moved` to return `false` on successive calls to `combine_ancient_slots_packed_internal`, while `target_slots_sorted` contains many legitimately packable slots that never get shrunk as a result. [5](#0-4)

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L390-415)
```rust
    fn many_ref_accounts_can_be_moved(
        many_refs_newest: &[AliveAccounts<'_>],
        target_slots_sorted: &[Slot],
        tuning: &PackedAncientStorageTuning,
    ) -> bool {
        let alive_bytes = many_refs_newest
            .iter()
            .map(|alive| alive.bytes)
            .sum::<usize>();
        let required_ideal_packed = (alive_bytes as u64 / tuning.ideal_storage_size + 1) as usize;
        if alive_bytes == 0 {
            // nothing required, so no problem moving nothing
            return true;
        }
        if target_slots_sorted.len() < required_ideal_packed {
            return false;
        }
        let i_last = target_slots_sorted
            .len()
            .saturating_sub(required_ideal_packed);

        let highest_slot = target_slots_sorted[i_last];
        many_refs_newest
            .iter()
            .all(|many| many.slot <= highest_slot)
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L440-482)
```rust
        if ancient_slot_infos.all_infos.is_empty() {
            return; // nothing to do
        }
        let mut accounts_per_storage = self
            .get_unique_accounts_from_storage_for_combining_ancient_slots(
                &ancient_slot_infos.all_infos[..],
            );

        let mut accounts_to_combine = self.calc_accounts_to_combine(
            &mut accounts_per_storage,
            &tuning,
            IncludeManyRefSlots::Skip,
        );
        metrics.unpackable_slots_count += accounts_to_combine.unpackable_slots_count;

        let mut many_refs_newest = accounts_to_combine
            .accounts_to_combine
            .iter_mut()
            .filter_map(|alive| {
                let newest_alive =
                    std::mem::take(&mut alive.alive_accounts.many_refs_this_is_newest_alive);
                (!newest_alive.accounts.is_empty()).then_some(newest_alive)
            })
            .collect::<Vec<_>>();

        // Sort highest slot to lowest slot. This way, we will put the multi ref accounts with the highest slots in the highest
        // packed slot.
        many_refs_newest.sort_unstable_by_key(|b| cmp::Reverse(b.slot));
        metrics.newest_alive_packed_count += many_refs_newest.len();

        if !Self::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &accounts_to_combine.target_slots_sorted,
            &tuning,
        ) {
            datapoint_info!("shrink_ancient_stats", ("high_slot", 1, i64));
            log::info!(
                "unable to ancient pack: highest available slot: {:?}, lowest required slot: {:?}",
                accounts_to_combine.target_slots_sorted.last(),
                many_refs_newest.last().map(|accounts| accounts.slot)
            );
            return;
        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L879-905)
```rust
                // There are alive accounts with ref_count > 1, where the entry for the account in the index is NOT the highest slot. (`many_refs_old_alive`)
                // This means this account must remain IN this slot. There could be alive or dead references to this same account in any older slot.
                // Moving it to a lower slot could move it before an alive or dead entry to this same account.
                // Moving it to a higher slot could move it ahead of other slots where this account is also alive. We know a higher slot exists that contains this account.
                // So, moving this account to a different slot could result in the moved account being before or after other instances of this account newer or older.
                // This would fail the invariant that the highest slot # where an account exists defines the most recent account.
                // It could be a clean error or a transient condition that will resolve if we encounter this situation.
                // The count of these accounts per call will be reported by metrics in `unpackable_slots_count`
                if shrink_collect.alive_accounts.one_ref.accounts.is_empty()
                    && shrink_collect
                        .alive_accounts
                        .many_refs_this_is_newest_alive
                        .accounts
                        .is_empty()
                {
                    // all accounts in this append vec are alive and have > 1 ref, so nothing to be done for this append vec
                    remove.push(i);
                    continue;
                }
                accounts_keep_slots
                    .insert(shrink_collect.slot, std::mem::take(many_refs_old_alive));
            } else {
                // No alive accounts in this slot have a ref_count > 1. So, ALL alive accounts in this slot can be written to any other slot
                // we find convenient. There is NO other instance of any account to conflict with.
                target_slots_sorted.push(shrink_collect.slot);
            }
        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1631-1730)
```rust
    fn test_calc_accounts_to_combine_many_refs() {
        // n storages
        // 1 account each
        // all accounts have 1 ref or all accounts have 2 refs
        let data_size = 48;
        let alive_bytes_per_slot = AppendVec::calculate_stored_size(data_size as usize) as u64;

        // pack 2.5 ancient slots into 1 packed slot ideally
        let tuning = PackedAncientStorageTuning {
            ideal_storage_size: NonZeroU64::new(alive_bytes_per_slot * 2 + 1).unwrap(),
            ..default_tuning()
        };
        for many_ref_slots in [IncludeManyRefSlots::Skip, IncludeManyRefSlots::Include] {
            for num_slots in 0..6 {
                for unsorted_slots in [false, true] {
                    for two_refs in [false, true] {
                        let (db, mut storages, _slots, mut infos) =
                            get_sample_storages(num_slots, Some(data_size));

                        infos.iter_mut().for_each(|a| {
                            a.alive_bytes += alive_bytes_per_slot;
                        });

                        if unsorted_slots {
                            storages = storages.into_iter().rev().collect();
                            infos = infos.into_iter().rev().collect();
                        }

                        let original_results = storages
                            .iter()
                            .map(|store| db.get_unique_accounts_from_storage(store))
                            .collect::<Vec<_>>();
                        if two_refs {
                            original_results.iter().for_each(|results| {
                                results.stored_accounts.iter().for_each(|account| {
                                    db.accounts_index.get_and_then(account.pubkey(), |entry| {
                                        (false, entry.unwrap().addref())
                                    });
                                })
                            });
                        }

                        let original_results = storages
                            .iter()
                            .map(|store| db.get_unique_accounts_from_storage(store))
                            .collect::<Vec<_>>();

                        let mut accounts_per_storage =
                            infos.iter().zip(original_results).collect::<Vec<_>>();

                        let accounts_to_combine = db.calc_accounts_to_combine(
                            &mut accounts_per_storage,
                            &tuning,
                            many_ref_slots,
                        );
                        let expected_accounts_to_combine = if num_slots >= 3
                            && two_refs
                            && many_ref_slots == IncludeManyRefSlots::Skip
                        {
                            // In this test setup, 2.5 regular slots fits into 1 ancient slot.
                            // When there are two_refs and when slots < 3, all regular slots can fit into one ancient slots.
                            // Therefore, we should have all slots that can be combined for slots < 3.
                            // However, when slots >=3, we need more than one ancient slots. The pack algorithm will need to first
                            // find at least [ceiling(num_slots/2.5) - 1] slots that's don't have many_refs before we can pack slots with many_refs.
                            // Since we decrease the number of alive bytes we'll be writing, when we encounter slots that can't be packed,
                            // we now reduce the number required ideal packed storages. As a result, the last
                            // slot can be packed, and the number of accounts to combine should be 2.
                            2
                        } else {
                            num_slots
                        };
                        (0..accounts_to_combine
                            .target_slots_sorted
                            .len()
                            .saturating_sub(1))
                            .for_each(|i| {
                                let slots = &accounts_to_combine.target_slots_sorted;
                                assert!(slots[i] < slots[i + 1]);
                            });

                        log::debug!(
                            "output slots: {:?}, num_slots: {num_slots}, two_refs: {two_refs}, \
                             many_refs: {many_ref_slots:?}, expected accounts to combine: \
                             {expected_accounts_to_combine}, target slots: {:?}, \
                             accounts_to_combine: {}",
                            accounts_to_combine.target_slots_sorted,
                            accounts_to_combine.target_slots_sorted,
                            accounts_to_combine.accounts_to_combine.len(),
                        );
                        assert_eq!(
                            accounts_to_combine.accounts_to_combine.len(),
                            expected_accounts_to_combine,
                            "num_slots: {num_slots}, two_refs: {two_refs}, many_refs: \
                             {many_ref_slots:?}"
                        );
                    }
                }
            }
        }
    }
```
