### Title
`many_ref_accounts_can_be_moved`'s byte-based storage estimate can undercount actual bins produced by `PackedAncientStorage::pack`, allowing newest-alive multi-ref accounts to be packed into a slot lower than their current slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`many_ref_accounts_can_be_moved` decides whether all `many_refs_newest` accounts can legally be relocated by estimating the number of packed storages they will require as `alive_bytes / ideal_storage_size + 1`, then checking that every account's current slot is `<=` the lowest of the top `required_ideal_packed` target slots. `PackedAncientStorage::pack` actually performs a "next-fit"-style bin pack that can consume more bins than this byte-based estimate whenever variable-sized accounts leave storages under-filled, so the real packed-storage boundary for many-ref data can shift into a lower target slot than the one validated by the pre-check.

### Finding Description
`many_ref_accounts_can_be_moved` (accounts-db/src/ancient_append_vecs.rs:390-415) computes `required_ideal_packed = alive_bytes / ideal_storage_size + 1` and then only verifies that every `many_refs_newest` account's slot is `<= target_slots_sorted[i_last]`, where `i_last = target_slots_sorted.len() - required_ideal_packed`. This is a coarse byte-budget check, not a simulation of the actual packing. [1](#0-0) 

The actual packing is done later by `PackedAncientStorage::pack` (accounts-db/src/ancient_append_vecs.rs:1012-1094), which walks the `many_refs_newest` (sorted newest-slot-first) chained with one-ref accounts and greedily fills each storage up to `ideal_size`. When the next candidate account does not fit in the remaining space of the current storage, that storage is closed under-filled and a new storage is started for that whole account (accounts are never split): [2](#0-1) 

Because this is essentially a next-fit bin-packing algorithm (not a perfectly tight ceil(total_bytes/ideal_size) packer), the number of storages it produces for the `many_refs_newest` portion can exceed the `+1` margin assumed by `many_ref_accounts_can_be_moved` whenever accounts have sizes that repeatedly leave a meaningful fraction of each storage unused (fragmentation). `write_packed_storages` then maps `packed_contents` in order to `target_slots_sorted.iter().rev()` (highest target slot first) (accounts-db/src/ancient_append_vecs.rs:657-662), so if packing many-ref data spills into one extra storage beyond what `required_ideal_packed` assumed, that extra storage is mapped to a *lower* target slot than the one validated by the pre-check. [3](#0-2) 

The only subsequent guard, in `combine_ancient_slots_packed_internal`, only checks the *total* pack length against the *total* number of target slots (`if pack.len() > accounts_to_combine.target_slots_sorted.len() { return; }`), not whether each many-ref account individually lands at a slot `>=` its original slot: [4](#0-3) 

So a `many_refs_newest` account whose slot is between `target_slots_sorted[i_last]` and `target_slots_sorted[i_last - 1]` can be silently packed into `target_slots_sorted[i_last - 1]` (a lower slot than validated, and potentially lower than the account's own original slot), violating the documented invariant "accounts in `many_refs_newest` must be moved a slot >= each account's current slot" stated directly in the comment above `many_ref_accounts_can_be_moved`.

### Impact Explanation
If a multi-ref account is written to a slot lower than its true current highest-alive slot, `AccountsDb`'s "highest slot for a pubkey defines the current value" invariant is broken: subsequent index rebuilds, hash calculation, and snapshot generation can pick up a stale/incorrect version of the account for a given fork, or place it ahead of/behind other slot entries for the same pubkey. This is a silent account state corruption bug (bounty category: incorrect/stale account version, hash/capitalization divergence) reachable purely through account writes and ancient-storage packing, not through any privileged path.

### Likelihood Explanation
Triggering the fragmentation gap requires the attacker to: (1) create the same pubkey across enough distinct rooted forks to build ref_count > 1, ensuring the newest alive index entry sits at a slot below the eventual packed target, and (2) accumulate enough ancient-storage-eligible data with account sizes chosen to defeat the coarse `alive_bytes / ideal_storage_size` estimate (e.g., accounts sized so each storage is left significantly under-filled). Given `ideal_storage_size` is ~128MB and max account size is bounded (~10MB), the fragmentation-induced extra storage only manifests once enough many-ref data (many tens of MB, spanning several ideal-size chunks) accumulates to exceed the `+1` slack built into `required_ideal_packed`. This makes the bug real but requires substantial account/storage volume and many forks/rewrites to reliably reproduce, so likelihood is low-to-moderate and resource-intensive rather than trivially exploitable in one transaction.

### Recommendation
Replace the byte-budget heuristic in `many_ref_accounts_can_be_moved` with an actual dry-run of `PackedAncientStorage::pack` (or a tight upper bound accounting for the maximum possible per-storage fragmentation loss, e.g. `ceil(alive_bytes / (ideal_storage_size - max_account_stored_size))`), and additionally add a hard post-pack assertion/verification in `combine_ancient_slots_packed_internal` that every account in `many_refs_newest` is written to a target slot `>=` its original slot before committing the write, aborting the pack (as already done for the coarse case) if the invariant would be violated.

### Proof of Concept
Property-based/unit test plan in `accounts-db/src/ancient_append_vecs.rs` test module:
```rust
#[test]
fn test_many_ref_accounts_can_be_moved_fragmentation_gap() {
    // Construct `many_refs_newest` as several AliveAccounts groups whose sizes are chosen
    // (e.g. slightly over ideal_storage_size/2) so that PackedAncientStorage::pack's
    // next-fit algorithm produces one MORE storage than
    // `alive_bytes / ideal_storage_size + 1` predicts.
    let ideal_size = NonZeroU64::new(1000).unwrap();
    let tuning = PackedAncientStorageTuning { ideal_storage_size: ideal_size, ..default_tuning() };

    // many_refs_newest: N groups each of size just over ideal_size/2 at descending slots.
    let many_refs_newest = build_fragmenting_alive_accounts(/* sizes causing 1 account/storage */);
    let alive_bytes: usize = many_refs_newest.iter().map(|a| a.bytes).sum();
    let required_ideal_packed = (alive_bytes as u64 / ideal_size.get() + 1) as usize;

    // target_slots_sorted sized exactly to the (under-)estimated requirement.
    let target_slots_sorted: Vec<Slot> = (0..required_ideal_packed as Slot).collect();

    assert!(AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest, &target_slots_sorted, &tuning
    )); // passes pre-check

    // Now actually run the packer and assert the real bin count.
    let packed = PackedAncientStorage::pack(many_refs_newest.iter(), ideal_size);
    assert!(
        packed.len() > required_ideal_packed,
        "pack() produced more storages ({}) than many_ref_accounts_can_be_moved assumed ({})",
        packed.len(), required_ideal_packed
    );

    // Simulate the write_packed_storages slot mapping and assert the invariant is violated
    // for at least one account: its assigned target slot < its original slot.
    let rev_targets: Vec<Slot> = target_slots_sorted.iter().rev().cloned().collect();
    for (i, storage) in packed.iter().enumerate() {
        if let Some(&assigned_slot) = rev_targets.get(i) {
            for (orig_slot, _accounts) in &storage.accounts {
                assert!(
                    assigned_slot >= *orig_slot,
                    "invariant violated: account originally at slot {} packed into lower slot {}",
                    orig_slot, assigned_slot
                );
            }
        } else {
            panic!("not enough target slots for produced packed storage {i}");
        }
    }
}
```
Expected result on current code: the final assertion loop fails (or the `packed.len() > required_ideal_packed` assertion fails), demonstrating that `many_ref_accounts_can_be_moved`'s estimate can be inconsistent with `PackedAncientStorage::pack`'s actual output, allowing a many-ref account to be assigned to a slot lower than its original slot.

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

**File:** accounts-db/src/ancient_append_vecs.rs (L506-509)
```rust
        if pack.len() > accounts_to_combine.target_slots_sorted.len() {
            // Not enough slots to contain the accounts we are trying to pack.
            return;
        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L646-662)
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

**File:** accounts-db/src/ancient_append_vecs.rs (L1046-1082)
```rust
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
```
