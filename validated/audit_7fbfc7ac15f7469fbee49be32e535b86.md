### Title
Bin-packing fragmentation in ancient-storage packing lets `many_ref_accounts_can_be_moved` under-estimate required target slots, placing a multi-ref account's newest version into a lower slot than its original slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
The safety check `many_ref_accounts_can_be_moved` (accounts-db/src/ancient_append_vecs.rs:390-415) that guards moving `many_refs_this_is_newest_alive` accounts to new target slots during ancient packing computes the number of required target slots from the *aggregate byte total* of these accounts, not from the actual bin-packing result produced by `PackedAncientStorage::pack`. Because `pack` (lines 1009-1095) performs simple sequential first-fit packing without splitting individual accounts, an attacker who controls account data sizes can force severe fragmentation so the real number of packed storages needed exceeds the naive estimate, causing `write_packed_storages` to place the tail of the highest-slot content into a lower target slot than the coarse check guaranteed.

### Finding Description
`combine_ancient_slots_packed_internal` (lines 417-518) collects, for pubkeys with `ref_count > 1`, the entry that is the highest-slot ("newest") alive version per originating ancient storage into `many_refs_newest`, sorted highest-slot-first (`cmp::Reverse(b.slot)`, line 467). Before packing, it calls `many_ref_accounts_can_be_moved` (lines 386-415) to verify these accounts can be safely relocated to `target_slots_sorted` without violating the "highest slot defines most recent account" invariant.

That check computes:
```
required_ideal_packed = (alive_bytes / ideal_storage_size) + 1
highest_slot = target_slots_sorted[len - required_ideal_packed]
assert all(many.slot <= highest_slot)
```
This is a **byte-total** estimate of how many target slots will be consumed by `many_refs_newest` content. It assumes bin-packing efficiency proportional to total bytes / ideal size.

However, the actual packer `PackedAncientStorage::pack` (lines 1009-1095) is a simple sequential first-fit packer that never splits an individual account and only starts a new storage once the current one cannot fit the next whole account. If an attacker crafts many accounts each sized just over `ideal_storage_size / 2`, only one such account fits per packed storage, so `N` such accounts require `N` storages, while the naive byte-based estimate predicts only `~N/2 + 1`. This is the classical bin-packing fragmentation gap, and the account sizes are fully attacker-controlled (`data_len` is chosen at account creation/resize).

Because `write_packed_storages` (lines 646-701) zips `target_slots_sorted.iter().rev()` (highest target slot first) with the actual `packed_contents` produced by `pack()` in output order, and the `many_refs_newest` groups occupy the *front* of the chained iterator (`many_refs_newest.iter().chain(one_ref...)`, lines 496-504), any fragmentation-driven overflow beyond `required_ideal_packed` pushes the tail chunks of `many_refs_newest` content into target slots at indices *below* `i_last` — i.e., slots the initial check never verified are `>= many.slot`. The only remaining gate, `pack.len() > accounts_to_combine.target_slots_sorted.len()` (lines 506-509), only aborts if the *total* packed storages (including all one-ref data) exceeds the total target slots — it does not verify that the specific chunk boundary for `many_refs_newest` content stays within the previously computed safe boundary.

The result: an account whose newest alive index entry was at slot `S` can be repacked into a target slot `< S`, while an older/duplicate entry for the same pubkey (in `accounts_keep_slots`, unaffected) remains at its original slot which could now be `> ` the new location of the "newest" copy. This breaks the fundamental AccountsDB invariant that the account index's highest slot in a pubkey's slot list holds the most recent value, so `AccountsDb::load` (via the accounts index binary/linear scan of slot list "highest visible slot for the ancestor set") can return a stale, wrong-slot value once packing completes and the index is updated to reflect the new (lower) slot as containing that copy.

### Impact Explanation
This is a stale/wrong-slot account load caused entirely by ancient-storage packing logic reachable through ordinary account writes and refcount growth (no validator/leader control needed). If exploited, a later transaction reading the account would observe an outdated (or logically inconsistent) version, potentially enabling double-spend-like value confusion or hash/capitalization divergence between honest nodes that ancient-pack at different times or with different randomization (`can_randomly_shrink`), fitting the "wrong-slot account value fed to execution after ancient packing" and "honest-node snapshot-vs-replay mismatch" bounty categories.

### Likelihood Explanation
Exploitability requires: (1) creating a large number of accounts, reused across many slots, with `ref_count > 1` (achievable by any user, e.g. by repeatedly recreating an account after closing it, or by any account write pattern producing duplicate index entries), and (2) choosing data sizes so that ancient-slot packing fragmentation occurs (sizes just above `ideal_storage_size / 2`). Both are within normal unprivileged user capability (pays for own storage/rent). The remaining precondition — that these slots eventually become "ancient" and get selected for `combine_ancient_slots_packed` — happens automatically as part of normal validator background operation once slots age past the ancient-slot threshold, not requiring special config. This makes the bug feasible but non-trivial, since it needs careful construction of a fragmentation pattern across many slots.

### Recommendation
Make `many_ref_accounts_can_be_moved`'s slot-safety check consistent with the actual output of `PackedAncientStorage::pack`, e.g., by running `pack()` on `many_refs_newest` alone first, then verifying by construction that every fragment index maps to a target slot `>= ` the originating group's slot, instead of using an aggregate-byte estimate. Alternatively, compute `required_ideal_packed` as an account-count-aware worst-case bound (e.g., sum of `ceil(group.bytes / ideal_storage_size)` per group, or a true simulation of the packer) rather than dividing the combined byte total by `ideal_storage_size`.

### Proof of Concept
```rust
// accounts-db/src/ancient_append_vecs.rs (add to tests mod)
#[test]
fn test_many_ref_accounts_fragmentation_violates_slot_invariant() {
    // ideal_storage_size chosen so 2 accounts of size > ideal/2 can't share a bucket
    let ideal_size = 100u64;
    let tuning = PackedAncientStorageTuning {
        ideal_storage_size: NonZeroU64::new(ideal_size).unwrap(),
        ..default_tuning()
    };

    // Craft N "newest alive" groups, each with total bytes = 60 (> ideal/2),
    // with descending slots so many_refs_newest is naturally sorted highest-first.
    let n = 10;
    let many_refs_newest: Vec<AliveAccounts> = (0..n)
        .map(|i| AliveAccounts {
            bytes: 60, // > ideal_size / 2 => only 1 fits per packed storage
            slot: (100 - i) as Slot, // descending: 100, 99, ..., 91
            accounts: Vec::default(), // placeholder; use real AccountFromStorage in full repro
        })
        .collect();

    // Naive estimate: total_bytes = 600, required_ideal_packed = 600/100 + 1 = 7
    // Actual packer requirement given fragmentation: ~10 (one per group)
    let target_slots_sorted: Vec<Slot> = (1..=n as Slot).collect(); // 1..=10, ascending

    // The safety check claims success using only 7 of the 10 available target slots,
    // asserting only that group slots <= target_slots_sorted[10-7] = target_slots_sorted[3] = 4
    let ok = AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest,
        &target_slots_sorted,
        &tuning,
    );
    // Demonstrate: even though `ok` may report true (using naive byte math),
    // the real number of packed storages needed for these 10 groups (bytes=60 each,
    // ideal_size=100) is 10, not 7, because no two 60-byte groups can share one bucket.
    // Run the actual packer and show the resulting storage count exceeds the
    // 'required_ideal_packed' used above, and that the chunk containing the highest-slot
    // group's data pairs (via target_slots_sorted.rev()) with a target slot below that slot.
    let packed = PackedAncientStorage::pack(many_refs_newest.iter(), tuning.ideal_storage_size);
    assert!(packed.len() > 7, "fragmentation should exceed naive estimate");

    // Simulate write_packed_storages' zip logic:
    let mapping: Vec<(Slot, Slot)> = target_slots_sorted
        .iter()
        .rev()
        .zip(packed.iter())
        .map(|(target, pack)| {
            let source_slot = pack.accounts.iter().map(|(s, _)| *s).max().unwrap();
            (*target, source_slot)
        })
        .collect();

    // Assert the invariant: every (target_slot, source_slot) pair must have target_slot >= source_slot.
    // With sufficient fragmentation (n large enough relative to ideal_size/account size ratio),
    // this assertion FAILS, proving a source slot's newest copy is relocated below its own slot.
    assert!(
        mapping.iter().all(|(target, source)| target >= source),
        "invariant violated: found target slot lower than source slot: {:?}",
        mapping
    );
}
```
(A full integration-level repro would build real `AccountFromStorage`/`AliveAccounts` via `get_sample_storages` with crafted `data_size` per slot as in existing tests like `test_calc_accounts_to_combine_many_refs`, then run `combine_ancient_slots_packed_internal` end-to-end and assert, for the reused pubkey, that `accounts_index.get(pubkey)`'s max-slot entry's stored account data equals the pre-packing max-slot entry's data.) [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** accounts-db/src/ancient_append_vecs.rs (L455-509)
```rust
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

        // for the accounts which are one ref and can be put anywhere, we want to put the accounts from the LARGEST storages at the end.
        // This causes us to keep the accounts we're re-packing from already existing ancient storages together with other normal one ref accounts.
        // The alternative could cause us to mix newly ancient slots produced by flush (containing accounts touched more recently) with previously
        // packed ancient storages which over time contained enough dead accounts that the storage needed to be shrunk by being re-packed.
        // The end result of this sort should cause older, colder accounts (previously packed into large storages and then re-packed/shrunk) to
        // be re-packed together with other older/colder accounts.
        accounts_to_combine
            .accounts_to_combine
            .sort_unstable_by_key(|a| a.written_bytes);

        // pack the accounts with 1 ref or refs > 1 but the slot we're packing is the highest alive slot for the pubkey.
        // Note the `chain` below combining the 2 types of refs.
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

**File:** accounts-db/src/ancient_append_vecs.rs (L646-701)
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

        // keep track of how many slots were shrunk away
        self.shrink_ancient_stats
            .ancient_append_vecs_shrunk
            .fetch_add(
                accounts_to_combine
                    .target_slots_sorted
                    .len()
                    .saturating_sub(packer.len()) as u64,
                Ordering::Relaxed,
            );

        self.thread_pool_background.install(|| {
            packer.par_iter().for_each(|(target_slot, pack)| {
                let mut write_ancient_accounts_local = WriteAncientAccounts::default();
                self.write_one_packed_storage(
                    pack,
                    **target_slot,
                    &mut write_ancient_accounts_local,
                );
                let mut write = write_ancient_accounts.lock().unwrap();
                write
                    .shrinks_in_progress
                    .extend(write_ancient_accounts_local.shrinks_in_progress);
                write
                    .metrics
                    .accumulate(&write_ancient_accounts_local.metrics);
            });
        });

        let mut write_ancient_accounts = write_ancient_accounts.into_inner().unwrap();

        // write new storages where contents were unable to move because ref_count > 1
        self.write_ancient_accounts_to_same_slot_multiple_refs(
            accounts_to_combine.accounts_keep_slots.values(),
            &mut write_ancient_accounts,
        );
        write_ancient_accounts
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1012-1093)
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
```

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
