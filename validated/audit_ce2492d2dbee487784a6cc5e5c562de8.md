### Title
Unbounded per-pubkey slot-list growth causes quadratic-cost account index updates while an accounts scan holds back reclaims - (File: accounts-db/src/accounts_index/in_mem_accounts_index.rs)

### Summary
The external report describes a Solidity contract where `fullyClaimReward()` loops over N reward tokens × M locks, and each inner `_claimReward()` call incorrectly re-triggers a modifier that performs the same O(N×M) loop, turning an O(N×M) operation into an O((N×M)²) one — a classic "nested-loop-inside-a-loop-per-item" gas-DoS pattern. The Agave analog is in `AccountsDb`'s index-update path: `InMemAccountsIndex::update_slot_list()` performs a full linear scan of a pubkey's `slot_list` on every single account write (`retain_and_count`), and this function is invoked once per account, per flush, from `update_index_for_flush`. Under `UpsertReclaim::IgnoreReclaims` — which is deliberately used whenever there is an ongoing scan, so reclaims are deferred (`store_accounts_for_flush`/`do_flush_slot_cache` comment: "There's an ongoing scan to avoid reclaiming accounts being scanned") — old slot-list entries are never pruned. This means repeated writes to the same account across many slots while a scan is outstanding cause the slot_list to grow unboundedly, and each subsequent write pays the cost of rescanning the entire (growing) list, yielding the same "loop inside a loop that's called once per prior loop iteration" cost profile as the reported Solidity bug.

### Finding Description
`InMemAccountsIndex::update_slot_list` is the function used by every account write to update the index's per-pubkey slot list: [1](#0-0) 

It calls `slot_list.retain_and_count(...)`, an O(len(slot_list)) scan that looks for the entry matching `old_slot` and, when `UpsertReclaim::ReclaimOldSlots` is used, additionally strips out any entries older than the new slot. When `UpsertReclaim::IgnoreReclaims` is used instead, no pruning happens at all and the slot_list simply keeps growing: [2](#0-1) 

This function is reached through `AccountsIndex::upsert`, which is invoked once per account inside `update_index_for_flush`'s per-account loop: [3](#0-2) 

The choice between `ReclaimOldSlots` (bounded slot_list) and `IgnoreReclaims` (unbounded slot_list) is made in `do_flush_slot_cache`/`store_accounts_for_flush`, explicitly to avoid reclaiming versions that an in-flight scan may still need: [4](#0-3) 

And `select_pubkeys_to_store` picks `PubkeysToStore::All` (which forces `IgnoreReclaims`) for any root above `max_clean_root`: [5](#0-4) 

`max_clean_root` itself is capped by the oldest in-flight scan's root via `min_ongoing_scan_root()`: [6](#0-5) 

So, for as long as any scan is active, every write to an account whose slot exceeds the (frozen) `max_clean_root` skips reclaiming, and `update_slot_list`'s O(n) `retain_and_count` scan runs against an ever-growing list on every subsequent write to that same pubkey. Writing to the same pubkey N times while a scan is outstanding costs O(1)+O(2)+...+O(N) = O(N²) total CPU, mirroring the reported pattern where an inner per-item operation (`_claimReward`) unexpectedly re-triggers the same-sized loop (`updateReward`) that the outer loop already paid for.

### Impact Explanation
This is a disproportionate CPU/storage cost bug: normal per-account index maintenance is expected to be O(1) amortized (slot lists are kept short by prompt reclaiming), but for the duration of any scan, the cost of updating a hot account's index entry degrades to O(n) per write and O(n²) in aggregate for n writes to that account, along with the corresponding growth in memory held by the slot list (each entry is an `AccountInfo`/`(Slot, T)` pair) until the scan completes and clean can catch up. On a validator processing a busy account (e.g., a frequently-written program or system account) during a long-running scan, this degrades index-update throughput and could contribute to falling behind on block processing (analogous to the reported DoS/expensive-gas impact), rather than any consensus-breaking state divergence.

### Likelihood Explanation
Reaching this condition does not require any privileged role: any transaction sender can write repeatedly to an account, and scans are a routine, unprivileged-triggerable Accounts-DB operation (e.g., a program-accounts scan). The severity scales with how long the scan is held open and how "hot" the targeted account is (number of writes to it during that window), so it requires a longer-running scan plus a busy account to produce a materially large slot list, making it a real but not immediate/day-one condition.

### Recommendation
Bound the cost of `update_slot_list` under `IgnoreReclaims` independent of scan duration — e.g., still prune purely-cached/duplicate stale entries opportunistically, or track slot lists with a data structure that supports O(1)/O(log n) lookup by slot instead of a linear scan, so that deferring reclaims during a scan doesn't turn per-write cost into an O(n) (and aggregate O(n²)) operation.

### Proof of Concept
1. Start a long-running scan on a bank (any scan that registers with `ScanTracker`, e.g. via `scan_accounts`/`index_scan_accounts`), so `min_ongoing_scan_root()` pins `max_clean_root` below the current root.
2. While the scan is outstanding, repeatedly submit transactions in successive rooted slots that write to the same target pubkey (N times).
3. Because `max_clean_root` is capped at the scan's root, `select_pubkeys_to_store` returns `PubkeysToStore::All` for roots above it, so `do_flush_slot_cache`/`store_accounts_for_flush` use `UpsertReclaim::IgnoreReclaims` for those flushes.
4. Each of the N writes calls `update_slot_list`, whose `retain_and_count` scans the target pubkey's slot list, which now has i entries at the i-th write — total scan work is O(1+2+...+N) = O(N²), and the slot list itself grows to N entries in memory instead of being kept short by `ReclaimOldSlots`.
5. Ending the scan lets `clean_accounts` catch up and shrink the slot list back down, confirming the growth was purely an artifact of the scan-deferred reclaim path rather than legitimate multi-fork state.

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L757-814)
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
```

**File:** accounts-db/src/accounts_db.rs (L1453-1465)
```rust
    fn max_clean_root(&self, proposed_clean_root: Option<Slot>) -> Option<Slot> {
        match (
            self.scan_tracker.min_ongoing_scan_root(),
            proposed_clean_root,
        ) {
            (None, None) => None,
            (Some(min_scan_root), None) => Some(min_scan_root),
            (None, Some(proposed_clean_root)) => Some(proposed_clean_root),
            (Some(min_scan_root), Some(proposed_clean_root)) => {
                Some(std::cmp::min(min_scan_root, proposed_clean_root))
            }
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L4436-4453)
```rust
        for &root in flushed_roots.iter().rev() {
            let cleaned = max_clean_root.is_none_or(|max_clean_root| root <= max_clean_root);
            let to_flush = if !cleaned {
                PubkeysToStore::All
            } else {
                let mut flush_keys = HashSet::default();
                if let Some(slot_cache) = self.accounts_cache.slot_cache(root) {
                    for entry in slot_cache.iter() {
                        let pubkey = *entry.key();
                        // If not seen in a newer root, this is the newest version, so flush it.
                        if written_accounts.insert(pubkey) {
                            flush_keys.insert(pubkey);
                        }
                    }
                }
                PubkeysToStore::Only(flush_keys)
            };
            pubkeys_to_store.insert(root, to_flush);
```

**File:** accounts-db/src/accounts_db.rs (L4508-4516)
```rust
        // Use ReclaimOldSlots to reclaim old slots if marking obsolete accounts and cleaning.
        // Cleaning is enabled if pubkeys_to_store is PubkeysToStore::Only
        // pubkeys_to_store is PubkeysToStore::All when
        // 1) There's an ongoing scan to avoid reclaiming accounts being scanned.
        // 2) The slot is > max_clean_root to prevent unrooted slots from reclaiming rooted versions.
        let reclaim_method = match pubkeys_to_store {
            PubkeysToStore::Only(_) => UpsertReclaim::ReclaimOldSlots,
            PubkeysToStore::All => UpsertReclaim::IgnoreReclaims,
        };
```

**File:** accounts-db/src/accounts_db.rs (L4899-4926)
```rust
        let update = |start, end| {
            let mut reclaims = ReclaimsSlotList::with_capacity((end - start) / 2);

            (start..end).for_each(|i| {
                let info: AccountInfo = infos[i];
                let old_slot = accounts.slot(i);
                let pubkey = accounts.pubkey(i);
                self.accounts_index.upsert(
                    target_slot,
                    old_slot,
                    pubkey,
                    info,
                    &mut reclaims,
                    reclaim,
                );

                if !self.account_indexes.is_empty() {
                    // Since StorableAccounts::account() may read the account from disk,
                    // avoid calling it unless secondary indexes are enabled.
                    accounts.account(i, |account| {
                        self.accounts_index.update_secondary_indexes(
                            pubkey,
                            &account,
                            &self.account_indexes,
                        );
                    });
                }
            });
```
