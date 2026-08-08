### Title
Fragmentation-based miscount in `many_ref_accounts_can_be_moved` can let many-ref "newest" accounts be repacked into a slot lower than their true newest slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`many_ref_accounts_can_be_moved` estimates how many packed target slots the `many_refs_newest` accounts will consume using a simple linear formula `alive_bytes / ideal_storage_size + 1`, but the actual placement is determined by `PackedAncientStorage::pack`, a sequential bin-packing algorithm that never splits an account across bins. When an attacker crafts account sizes that are just over half of `ideal_storage_size`, each bin holds only one account (≈50% fill), so the real number of bins used is roughly double what the linear formula predicts. This mismatch lets the guard function return `true` even though the real packing will push a `many_refs_newest` account into a target slot lower than its true (highest alive) slot, violating the documented slot-monotonicity invariant.

### Finding Description
`combine_ancient_slots_packed_internal` (accounts-db/src/ancient_append_vecs.rs:417-518) collects `many_refs_newest` accounts (ref_count > 1, but this is the highest-slot alive entry for the pubkey, per `ShrinkCollectAliveSeparatedByRefs::add`, accounts-db/src/accounts_db.rs:214-235), sorts them highest-slot-first, and calls `many_ref_accounts_can_be_moved` (ancient_append_vecs.rs:390-415) to validate that these accounts can still be moved into the highest slots of `target_slots_sorted` without breaking the invariant that "the highest slot # where an account exists defines the most recent account" (comment at ancient_append_vecs.rs:879-886).

`many_ref_accounts_can_be_moved` computes:
```
required_ideal_packed = (alive_bytes / ideal_storage_size) + 1
i_last = target_slots_sorted.len() - required_ideal_packed
highest_slot = target_slots_sorted[i_last]
```
and accepts if every `many_refs_newest` account's original slot is `<= highest_slot` [1](#0-0) .

This assumes the actual packer will place all `many_refs_newest` accounts within the first `required_ideal_packed` bins (thus into the top `required_ideal_packed` target slots). But `PackedAncientStorage::pack` (ancient_append_vecs.rs:1009-1095) is a strict, ordered bin-packing routine: once adding a whole account to the current bin would exceed `ideal_size`, the bin is closed with `full = true` and remaining space is wasted (never revisited) [2](#0-1) . If an attacker crafts N accounts, each roughly `ideal_size/2 + 1` bytes, spread across N distinct many-ref "newest" slots, each bin will hold exactly one such account (≈50% utilization), so N bins are actually required — while the linear estimate `alive_bytes/ideal_size + 1 ≈ N/2 + 1` undercounts by roughly a factor of two.

Because `write_packed_storages` zips packed bins (highest-slot many-ref data emitted first) with `target_slots_sorted.iter().rev()` (highest slot first) [3](#0-2) , an under-estimated `required_ideal_packed` causes `highest_slot` to be picked too high (too lenient), passing the `many.slot <= highest_slot` check, while the real packing consumes more bins than assumed and pushes lower-priority `many_refs_newest` accounts into target slots further down `target_slots_sorted` — potentially below their original slot. This breaks the documented precondition ("accounts in `many_refs_newest` must be moved a slot >= each account's current slot") without any other runtime check catching it; the only other guard, `pack.len() > accounts_to_combine.target_slots_sorted.len()` in `combine_ancient_slots_packed_internal` (ancient_append_vecs.rs:506-509), only checks the *total* bin count against *total* target slots, not per-account slot assignment.

Since accounts with older versions of the same pubkey that are *not* the "newest alive" entry are kept in place in `accounts_keep_slots` (`many_refs_old_alive`, at their original slot, ancient_append_vecs.rs:869-899) rather than moved, if such a kept slot lies strictly between the wrongly-chosen (too low) new target slot and the account's true original slot, the accounts index would then present the stale/older entry as the higher-slot (hence "latest") version for that pubkey.

### Impact Explanation
If exploited, this can cause `AccountsDb`'s account index to report an older, obsolete version of a multi-referenced pubkey as the most recent, which is a "wrong-version account load" — matching Agave's stale/incorrect-state bounty category. It does not require validator/leader control; the attacker only needs to create/rewrite accounts they own across many slots with carefully chosen data sizes and wait for periodic ancient-slot packing (`shrink_ancient_slots` → `combine_ancient_slots_packed`) to run.

### Likelihood Explanation
Exploitation requires: (1) many rooted slots aging into "ancient" status containing the same ref-counted pubkey, (2) account sizes engineered to be just over half of the dynamically-computed `ideal_storage_size` (which itself is derived from total alive ancient bytes and `max_ancient_slots`, so somewhat attacker-influenceable but also somewhat noisy/shared state), and (3) enough such accounts (N large enough) for the linear-vs-actual bin count gap to exceed the fixed "+1" margin. This is a non-trivial, multi-step, timing-dependent setup (attacker doesn't control exactly when ancient packing runs, nor the exact `ideal_storage_size` at that time, since it's derived from aggregate ancient-slot state shared with other accounts), making this a moderate-difficulty but plausible attack given sufficient account/slot volume and iteration/observation of `ideal_storage_size` via public state.

### Recommendation
Replace the linear byte-based estimate in `many_ref_accounts_can_be_moved` with either: (a) an actual dry-run call to `PackedAncientStorage::pack` restricted to the `many_refs_newest` accounts, using its true resulting bin count instead of an approximation, or (b) a per-account/per-bin slot check after `PackedAncientStorage::pack` produces the real `pack` vector in `combine_ancient_slots_packed_internal`, verifying for each `many_refs_newest` account that its final assigned target slot (as would result from `write_packed_storages`'s bin ↔ slot zip) is `>=` its original slot, aborting the packing pass otherwise.

### Proof of Concept
Rust unit test plan (add to `accounts-db/src/ancient_append_vecs.rs` tests module, alongside `test_many_ref_accounts_can_be_moved`):
```rust
#[test]
fn test_many_ref_accounts_can_be_moved_fragmentation_undercount() {
    // ideal_storage_size chosen so that an account of size (ideal/2 + 1)
    // only allows 1 such account per bin.
    let ideal = 1000u64;
    let tuning = PackedAncientStorageTuning {
        ideal_storage_size: NonZeroU64::new(ideal).unwrap(),
        ..default_tuning()
    };
    let account_bytes = (ideal / 2 + 1) as usize;
    let n = 6usize; // number of many-ref "newest" accounts/slots
    // simulate N separate many_refs_newest groups, each 1 account of account_bytes,
    // at strictly increasing slots 10, 20, 30, ...
    let many_refs_newest: Vec<AliveAccounts> = (0..n)
        .map(|i| AliveAccounts {
            bytes: account_bytes,
            slot: (i as u64 + 1) * 10,
            accounts: Vec::default(),
        })
        .collect();

    // Enough target slots to *appear* sufficient per the linear formula,
    // but not per real bin-packing behavior.
    // linear estimate: alive_bytes/ideal + 1 ~= n/2 + 1
    // real bins needed (via PackedAncientStorage::pack semantics): n
    let target_slots_sorted: Vec<Slot> = (0..(n / 2 + 2))
        .map(|i| 5 + (i as u64) * 10) // ascending, deliberately fewer than n
        .collect();

    // Assert the guard function WRONGLY approves this scenario:
    assert!(AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest,
        &target_slots_sorted,
        &tuning,
    ));

    // Now assert the *actual* packer needs more bins than target_slots_sorted.len(),
    // meaning the real assignment cannot satisfy monotonicity for all accounts
    // (some low-priority many_refs_newest bin will map to a target slot lower
    // than that account's original slot, or will exceed available slots).
    let packed = PackedAncientStorage::pack(many_refs_newest.iter(), tuning.ideal_storage_size);
    assert!(
        packed.len() > target_slots_sorted.len()
            || {
                // if packed.len() <= target_slots_sorted.len(), directly verify
                // per-bin target slot vs original slot monotonicity as
                // write_packed_storages would assign them (highest bin -> highest slot).
                let rev_targets: Vec<Slot> = target_slots_sorted.iter().rev().cloned().collect();
                packed.iter().enumerate().any(|(bin_idx, p)| {
                    let assigned_slot = rev_targets[bin_idx];
                    p.accounts.iter().any(|(orig_slot, _)| *orig_slot > assigned_slot)
                })
            },
        "expected monotonicity violation or insufficient slots, but packing succeeded safely"
    );
}
```
Expected result: the first assertion shows `many_ref_accounts_can_be_moved` returns `true` (incorrectly approving), while the second assertion demonstrates that the real `PackedAncientStorage::pack` output either needs more bins than available target slots, or would assign at least one `many_refs_newest` account to a target slot lower than its original slot — confirming the slot-monotonicity invariant violation predicted by the analysis.

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L394-415)
```rust
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

**File:** accounts-db/src/ancient_append_vecs.rs (L656-662)
```rust
        // iterate slots in highest to lowest
        let packer = accounts_to_combine
            .target_slots_sorted
            .iter()
            .rev()
            .zip(packed_contents)
            .collect::<Vec<_>>();
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1053-1067)
```rust
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
```
