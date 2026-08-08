### Title
Ancient-packing bin-count underestimate lets `many_refs_this_is_newest_alive` accounts land in a target slot lower than a retained `many_refs_old_alive` entry, causing stale/wrong-slot loads - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`combine_ancient_slots_packed_internal` protects multi-reference "newest" accounts from being moved to a slot lower than their current one by checking `many_ref_accounts_can_be_moved`, which estimates the number of packed storages the newest-alive accounts will need using a simple `alive_bytes / ideal_size + 1` formula. The actual bin-packer, `PackedAncientStorage::pack`, uses an online next-fit-style algorithm with no repacking/reordering, whose real bin count can exceed that estimate when item (account) sizes are adversarially chosen. When this happens, some `many_refs_this_is_newest_alive` accounts get written into a packed storage mapped to a target slot lower than the reserved "highest_slot" watermark, while the corresponding `many_refs_old_alive` entry for the same pubkey stays fixed at its original (now numerically higher) slot, silently reversing which entry the accounts index treats as "newest".

### Finding Description
Categorization in `ShrinkCollectAliveSeparatedByRefs::add` guarantees that an account is placed into `many_refs_this_is_newest_alive` only if its current slot is the maximum slot in that pubkey's slot list [1](#0-0) . The repacking algorithm relies on `many_ref_accounts_can_be_moved` to guarantee these accounts are only ever moved to a target slot `>=` their own slot, by checking that `target_slots_sorted[i_last]` (the boundary of a reserved block of `required_ideal_packed` highest target slots) is `>=` every `many_refs_newest` account's slot [2](#0-1) . `required_ideal_packed` is computed as `alive_bytes / ideal_size + 1`, an estimate that is only a valid upper bound if the actual packer achieves near-optimal (ceiling) bin packing.

`PackedAncientStorage::pack`, however, performs a strictly sequential, single-pass ("next-fit") packing: it fills the current bin until the next single account would overflow `ideal_size`, then starts a new bin, with no look-ahead or reordering of items across bins [3](#0-2) . This class of algorithm can require close to 2x the optimal number of bins when item sizes are adversarially chosen (e.g., alternating items just over half of `ideal_size`), so the real bin count for the `many_refs_newest` portion of the input can exceed the `required_ideal_packed` reservation validated earlier.

`combine_ancient_slots_packed_internal` only aborts if the *total* number of packed storages (`pack.len()`, covering both `many_refs_newest` and `one_ref` data) exceeds the *total* number of available target slots [4](#0-3) ; it never re-validates that the `many_refs_newest` portion still fits within the reserved high-slot region after the real pack() runs. `write_packed_storages` then maps `packed_contents` positionally onto `target_slots_sorted.iter().rev()` (highest slot first) [5](#0-4) , so if the many-refs data overflowed its reserved bin count, some of its accounts silently get mapped to a lower target slot than intended — potentially lower than a `many_refs_old_alive` entry for the same pubkey, which is written back unchanged to its original slot via `write_ancient_accounts_to_same_slot_multiple_refs` [6](#0-5) .

Since AccountsDb/the index treat the highest slot in a pubkey's slot list as authoritative, this reordering causes the stale `many_refs_old_alive` copy (now at a numerically higher slot) to be treated as canonical over the actually-newer data, resulting in stale/wrong-version account state being returned to subsequent loads.

An unprivileged attacker can influence every input to this calculation: `ideal_storage_size` is itself derived from `total_alive_bytes` across ancient slots which the attacker controls by creating/resizing many accounts [7](#0-6) , and account data sizes (hence `stored_size()`) are fully attacker-chosen. The attacker can also keep multiple rooted, alive versions of the same pubkey outstanding (before `clean` removes duplicates) by repeatedly rewriting an account they own across many slots, producing `ref_count > 1` entries feeding `many_refs_this_is_newest_alive` / `many_refs_old_alive`.

### Impact Explanation
This is a wrong-version/stale account load in AccountsDB: after ancient packing, a pubkey's canonical (highest-slot) entry in the index can point to older data than what should be current, silently reverting balance/state changes without any consensus-level detection, matching the "AccountsDB returns stale, wrong-slot ... state to execution" bounty category described in the question's scope.

### Likelihood Explanation
Exploitation requires: (1) the attacker to keep several rooted, still-alive versions of the same pubkey around long enough to become "ancient" and reach `combine_ancient_slots_packed`/`combine_ancient_slots_packed_internal`, and (2) carefully sized accounts to trigger next-fit's worst-case bin-count blowup relative to the `required_ideal_packed` estimate, while the *total* pack length still stays within `target_slots_sorted.len()` so the outer safety check does not trip. This is a non-trivial but purely user-side construction (no validator/leader control needed) using only account creation, resizing, and rewriting that a normal fee-payer can perform, making it feasible with sufficient account/storage layout engineering, though it requires precise crafting rather than being trivially trigger-able by accident.

### Recommendation
Replace the aggregate ceiling estimate in `many_ref_accounts_can_be_moved` with a validation based on the real output of `PackedAncientStorage::pack` (run/pre-compute the actual bin assignment for the `many_refs_newest` accounts alone, or pack them into their own dedicated bins first and check that resultant bin count fits in the reserved high-slot region) rather than an `alive_bytes / ideal_size` approximation. Alternatively, after `PackedAncientStorage::pack` runs, re-verify per-account that each `many_refs_newest` account's assigned target slot is `>=` its original slot before writing, and abort/re-split packing if any violate the invariant.

### Proof of Concept
Add a fuzz/invariant test in `accounts-db/src/ancient_append_vecs.rs` alongside `test_combine_ancient_slots_packed_internal`:
1. Construct several "ancient" slots where each contains one `ref_count = 2` account per slot, with the account in the highest slot marked `many_refs_this_is_newest_alive` and the account in a lower slot marked `many_refs_old_alive` for the *same pubkey* (as done in `test_calc_accounts_to_combine_many_refs`/`test_calc_accounts_to_combine_opposite`), but choose account data sizes so that each account's `stored_size()` is deliberately just over half of the computed `ideal_storage_size` (alternating with small filler accounts), reproducing next-fit's worst case.
2. Call `combine_ancient_slots_packed_internal` with these storages, mirroring `combine_ancient_slots_packed_for_tests`.
3. After packing, read back the accounts index slot-list for the shared pubkey and assert the entry with the highest lamport/version data also has the highest slot number (i.e., assert `many_refs_this_is_newest_alive`'s new slot >= the retained `many_refs_old_alive` slot).
4. Also assert `get_all_accounts`/`compare_all_accounts` still matches pre-packing state, and that `lt_hash_account`-derived hashes for the pubkey are unchanged — expect these assertions to fail once the crafted sizes exceed `required_ideal_packed`'s estimate, demonstrating the wrong-slot placement.

### Citations

**File:** accounts-db/src/accounts_db.rs (L214-235)
```rust
    fn add(
        &mut self,
        ref_count: RefCount,
        account: &'a AccountFromStorage,
        slot_list: &[(Slot, AccountInfo)],
    ) {
        let other = if ref_count == 1 {
            &mut self.one_ref
        } else if slot_list.len() == 1
            || !slot_list
                .iter()
                .any(|(slot_list_slot, _info)| slot_list_slot > &self.many_refs_old_alive.slot)
        {
            // this entry is alive but is newer than any other slot in the index
            &mut self.many_refs_this_is_newest_alive
        } else {
            // This entry is alive but is older than at least one other slot in the index.
            // We would expect clean to get rid of the entry for THIS slot at some point, but clean hasn't done that yet.
            &mut self.many_refs_old_alive
        };
        other.add(ref_count, account, slot_list);
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L386-415)
```rust
    /// return false if `many_refs_newest` accounts cannot be moved into `target_slots_sorted`.
    /// The slot # would be violated.
    /// accounts in `many_refs_newest` must be moved a slot >= each account's current slot.
    /// If that can be done, this fn returns true
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

**File:** accounts-db/src/ancient_append_vecs.rs (L522-538)
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
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L646-663)
```rust
    fn write_packed_storages<'a, 'b>(
        &'a self,
        accounts_to_combine: &'b AccountsToCombine<'b>,
        packed_contents: Vec<PackedAncientStorage<'b>>,
    ) -> WriteAncientAccounts<'a> {
        let write_ancient_accounts = Mutex::new(WriteAncientAccounts::default());

        // ok if we have more slots, but NOT ok if we have fewer slots than we have contents
        assert!(accounts_to_combine.target_slots_sorted.len() >= packed_contents.len());
        // write packed storages containing contents from many original slots
        // iterate slots in highest to lowest
        let packer = accounts_to_combine
            .target_slots_sorted
            .iter()
            .rev()
            .zip(packed_contents)
            .collect::<Vec<_>>();

```

**File:** accounts-db/src/ancient_append_vecs.rs (L958-971)
```rust
    fn write_ancient_accounts_to_same_slot_multiple_refs<'a, 'b: 'a>(
        &'b self,
        accounts_to_combine: impl Iterator<Item = &'a AliveAccounts<'a>>,
        write_ancient_accounts: &mut WriteAncientAccounts<'b>,
    ) {
        for alive_accounts in accounts_to_combine {
            let packed = PackedAncientStorage {
                bytes: alive_accounts.bytes as u64,
                accounts: vec![(alive_accounts.slot, &alive_accounts.accounts[..])],
            };

            self.write_one_packed_storage(&packed, alive_accounts.slot, write_ancient_accounts);
        }
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1009-1094)
```rust
impl<'a> PackedAncientStorage<'a> {
    /// return a minimal set of 'PackedAncientStorage's to contain all 'accounts_to_combine' with
    /// the new storages having a size guided by 'ideal_size'
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
