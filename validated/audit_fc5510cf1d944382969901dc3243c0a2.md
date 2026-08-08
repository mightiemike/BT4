### Title
Adversarial account-size distributions can force `PackedAncientStorage::pack` to exceed `target_slots_sorted.len()`, causing `combine_ancient_slots_packed_internal` to abort and redo the full ancient-packing scan every cycle - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`PackedAncientStorage::pack` implements a strictly sequential ("next-fit"-style) bin-packing algorithm over the concatenated list of alive accounts, not a size-aware/first-fit-decreasing packer. [1](#0-0)  When the resulting number of packed storages exceeds `accounts_to_combine.target_slots_sorted.len()`, `combine_ancient_slots_packed_internal` silently aborts the whole operation without writing anything, discarding all the scan/collection work already performed that cycle. [2](#0-1) 

### Finding Description
`combine_ancient_slots_packed_internal` runs the background ancient-packing pipeline: it first performs `collect_sort_filter_ancient_slots`, computes `tuning.ideal_storage_size` dynamically as `total_alive_bytes * 2 / max_ancient_slots` (bounded below by `self.ancient_storage_ideal_size`), then does `get_unique_accounts_from_storage_for_combining_ancient_slots` (a full read of every account in every candidate ancient storage) and `calc_accounts_to_combine`. [3](#0-2) [4](#0-3) 

The actual packing then happens in `PackedAncientStorage::pack`, which walks the chained iterator of `AliveAccounts` and greedily fills a bin up to `ideal_size`, moving to a new bin only when adding the next account would overflow it. [5](#0-4)  This is a classic "next-fit" packing strategy, whose known worst-case behavior can require close to 2x the number of bins that an optimal packer would need (e.g., when most items are sized just over half the bin capacity, so each bin can only fit one item and wastes nearly half its capacity). Since the built-in safety margin in `ideal_storage_size` is exactly a factor of 2 (`total_alive_bytes * 2 / max_ancient_slots`), an attacker who fills accounts with sizes chosen to induce close-to-2x fragmentation in this next-fit packer can push `pack.len()` right up to or past `target_slots_sorted.len()`.

When that happens, the code takes the early-return path:
```
if pack.len() > accounts_to_combine.target_slots_sorted.len() {
    // Not enough slots to contain the accounts we are trying to pack.
    return;
}
``` [6](#0-5) 
No storages are written, `finish_combine_ancient_slots_packed_internal` is never called, and nothing about the on-disk/index state changes. On the very next background invocation of `combine_ancient_slots_packed`, the same candidate slots (still containing the same adversarially-sized accounts) will again be scanned end-to-end (`calc_ancient_slot_info`, full account reads via `get_unique_accounts_from_storage_for_shrink`, `calc_accounts_to_combine`, and `pack`) and can again hit the same failure, since nothing in the algorithm adapts `ideal_storage_size` or slot selection specifically to break the adversarial pattern. [7](#0-6) 

No existing guard (slot/ancestor, zero-lamport, obsolete-account, or ref-count checks) mitigates this: those checks affect correctness of which accounts are alive/movable, not the bin-packing feasibility check itself. `many_ref_accounts_can_be_moved` is a separate, unrelated early-return guard for multi-ref accounts. [8](#0-7) 

Attacker feasibility: this only requires an unprivileged user creating accounts with sizes they fully control (`account_size` / `data_len`, which determines `stored_size()`), rooted normally through ordinary transactions, no special privileges needed. The attacker can iterate this every epoch cycle as their accounts naturally age into ancient-eligible slots.

### Impact Explanation
This is a background-job liveness/DoS issue: recurring, attacker-inducible CPU expenditure in the ancient-packing background thread (`shrink_ancient_stats`-tracked path) with no forward progress on compaction, i.e., a disproportionate storage/CPU cost issue relative to the attacker's low fee expenditure (in-scope bounty category: "disproportionate storage and CPU cost"). It does not corrupt state, cause stale reads, or produce hash/capitalization divergence — the impact is confined to wasted validator CPU cycles in the ancient-storage squash/pack pipeline.

### Likelihood Explanation
Feasible in principle for an attacker who can precisely control the sizes of a large set of accounts they own, timed so that many of them become part of the same `combine_ancient_slots_packed_internal` call with sizes clustered just above half of `ideal_storage_size` (which itself is recomputed from `total_alive_bytes`, giving the attacker some influence but not full determinism since it's derived from all candidate slots' aggregate bytes, not just the attacker's). Achieving *repeated* multi-cycle failure (not just an occasional isolated abort) is harder because `ideal_storage_size` is recalculated each cycle based on current totals, and any change in the account population between cycles (from clean/shrink or other legitimate activity) could shift the packing away from the adversarial worst case. This makes sustained, deterministic exploitation across "successive background cycles" uncertain without further empirical/fuzz confirmation — the report's own "Proof idea" acknowledges this needs a fuzz/invariant test to establish bounded retries, which I could not execute here.

### Recommendation
Replace the next-fit packing strategy in `PackedAncientStorage::pack` with a first-fit-decreasing (or similar) heuristic that sorts accounts by size before packing, which has a materially better worst-case approximation ratio and reduces the chance of adversarial fragmentation forcing `pack.len()` above `target_slots_sorted.len()`. Additionally, when the abort condition (`pack.len() > target_slots_sorted.len()`) triggers, increase `tuning.ideal_storage_size` or otherwise adapt bin sizing for a fallback re-pack attempt within the same cycle instead of unconditionally aborting and waiting for the next full-scan cycle, to guarantee convergence.

### Proof of Concept
Add a Rust unit test alongside the existing `test_pack_ancient_storages_varying` tests in `accounts-db/src/ancient_append_vecs.rs`:
1. Construct `num_slots` `AliveAccounts` groups where every account's `stored_size()` is `ideal_size/2 + 1` (i.e., just over half the ideal bin size), so no two accounts can share a bin.
2. Call `PackedAncientStorage::pack(accounts_to_combine.iter(), NonZeroU64::new(ideal_size).unwrap())`.
3. Assert `result.len()` approaches `num_slots` (i.e., no compaction benefit) rather than `ceil(total_bytes / ideal_size)`, demonstrating near-2x fragmentation versus optimal.
4. As an invariant/fuzz test, wire this account-size generator into `combine_ancient_slots_packed_internal` (via `combine_ancient_slots_packed_for_tests` helper already present in the test module) across repeated invocations with the same adversarial slot set, and assert that after N cycles the number of ancient slots strictly decreases (liveness), failing if the early return at line 506-509 triggers on every cycle without progress.

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

**File:** accounts-db/src/ancient_append_vecs.rs (L496-509)
```rust
        let pack = PackedAncientStorage::pack(
            many_refs_newest.iter().chain(
                accounts_to_combine
                    .accounts_to_combine
                    .iter()
                    .map(|shrink_collect| &shrink_collect.alive_accounts.one_ref),
            ),
            tuning.ideal_storage_size,
        );

        if pack.len() > accounts_to_combine.target_slots_sorted.len() {
            // Not enough slots to contain the accounts we are trying to pack.
            return;
        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L522-537)
```rust
    fn collect_sort_filter_ancient_slots(
        &self,
        slots: Vec<Slot>,
        tuning: &mut PackedAncientStorageTuning,
    ) -> AncientSlotInfos {
        let mut ancient_slot_infos = self.calc_ancient_slot_info(slots, tuning);
        // ideal storage size is total alive bytes of ancient storages
        // divided by half of max ancient slots
        tuning.ideal_storage_size = NonZeroU64::new(
            (ancient_slot_infos.total_alive_bytes.0 * 2 / tuning.max_ancient_slots.max(1) as u64)
                .max(self.ancient_storage_ideal_size),
        )
        .unwrap();

        ancient_slot_infos.filter_ancient_slots(tuning, &self.shrink_ancient_stats);
        ancient_slot_infos
```

**File:** accounts-db/src/ancient_append_vecs.rs (L705-720)
```rust
    fn get_unique_accounts_from_storage_for_combining_ancient_slots<'a>(
        &self,
        ancient_slots: &'a [SlotInfo],
    ) -> Vec<(&'a SlotInfo, GetUniqueAccountsResult)> {
        let mut accounts_to_combine = Vec::with_capacity(ancient_slots.len());

        for info in ancient_slots {
            let unique_accounts = self.get_unique_accounts_from_storage_for_shrink(
                &info.storage,
                &self.shrink_ancient_stats.shrink_stats,
            );
            accounts_to_combine.push((info, unique_accounts));
        }

        accounts_to_combine
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1012-1094)
```rust
    fn pack(
        mut accounts_to_combine: impl Iterator<Item = &'a AliveAccounts<'a>>,
        ideal_size: NonZeroU64,
    ) -> Vec<PackedAncientStorage<'a>> {
        let mut result = Vec::default();
        let ideal_size: u64 = ideal_size.into();
        let ideal_size = ideal_size as usize;
        let mut current_alive_accounts = accounts_to_combine.next();
        // starting at first entry in current_alive_accounts
        let mut partial_inner_index = 0;
        // 0 bytes written so far from the current set of accounts
        let mut partial_bytes_written = Saturating(0);
        // pack a new storage each iteration of this outer loop
        loop {
            let mut bytes_total = 0usize;
            let mut accounts_to_write = Vec::default();

            // walk through each set of alive accounts to pack the current new storage up to ideal_size
            let mut full = false;
            while !full && current_alive_accounts.is_some() {
                let alive_accounts = current_alive_accounts.unwrap();
                if partial_inner_index >= alive_accounts.accounts.len() {
                    // current_alive_accounts have all been written, so advance to next set from accounts_to_combine
                    current_alive_accounts = accounts_to_combine.next();
                    // reset partial progress since we're starting over with a new set of alive accounts
                    partial_inner_index = 0;
                    partial_bytes_written = Saturating(0);
                    continue;
                }
                let bytes_remaining_this_slot =
                    alive_accounts.bytes.saturating_sub(partial_bytes_written.0);
                let bytes_total_with_this_slot =
                    bytes_total.saturating_add(bytes_remaining_this_slot);
                let mut partial_inner_index_max_exclusive;
                if bytes_total_with_this_slot <= ideal_size {
                    partial_inner_index_max_exclusive = alive_accounts.accounts.len();
                    bytes_total = bytes_total_with_this_slot;
                } else {
                    partial_inner_index_max_exclusive = partial_inner_index;
                    // adding all the alive accounts in this storage would exceed the ideal size, so we have to break these accounts up
                    // look at each account and stop when we exceed the ideal size
                    while partial_inner_index_max_exclusive < alive_accounts.accounts.len() {
                        let account = alive_accounts.accounts[partial_inner_index_max_exclusive];
                        let account_size = account.stored_size();
                        let new_size = bytes_total.saturating_add(account_size);
                        if new_size > ideal_size && bytes_total > 0 {
                            full = true;
                            // partial_inner_index_max_exclusive is the index of the first account that puts us over the ideal size
                            // so, save it for next time
                            break;
                        }
                        // this account fits
                        partial_bytes_written += account_size;
                        bytes_total = new_size;
                        partial_inner_index_max_exclusive += 1;
                    }
                }

                if partial_inner_index < partial_inner_index_max_exclusive {
                    // these accounts belong in the current packed storage we're working on
                    accounts_to_write.push((
                        alive_accounts.slot,
                        // maybe all alive accounts from the current or could be partial
                        &alive_accounts.accounts
                            [partial_inner_index..partial_inner_index_max_exclusive],
                    ));
                }
                // start next storage with the account we ended with
                // this could be the end of the current alive accounts or could be anywhere within that vec
                partial_inner_index = partial_inner_index_max_exclusive;
            }
            if accounts_to_write.is_empty() {
                // if we returned without any accounts to write, then we have exhausted source data and have packaged all the storages we need
                break;
            }
            // we know the full contents of this packed storage now
            result.push(PackedAncientStorage {
                bytes: bytes_total as u64,
                accounts: accounts_to_write,
            });
        }
        result
    }
```

**File:** accounts-db/src/accounts_db.rs (L2524-2535)
```rust
    pub(crate) fn get_unique_accounts_from_storage_for_shrink(
        &self,
        store: &AccountStorageEntry,
        stats: &ShrinkStats,
    ) -> GetUniqueAccountsResult {
        let (result, storage_read_elapsed_us) =
            measure_us!(self.get_unique_accounts_from_storage(store));
        stats
            .storage_read_elapsed
            .fetch_add(storage_read_elapsed_us, Ordering::Relaxed);
        result
    }
```
