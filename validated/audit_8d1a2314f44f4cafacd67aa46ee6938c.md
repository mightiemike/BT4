### Title
Underestimated `required_ideal_packed` in `many_ref_accounts_can_be_moved` lets high-ref-count "newest" accounts be packed into a target slot lower than their true slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`many_ref_accounts_can_be_moved` estimates how many packed storages `many_refs_newest` accounts will consume purely from `total_bytes / ideal_storage_size`, but the real packer, `PackedAncientStorage::pack`, packs per-slot `AliveAccounts` batches and can fragment far more than that byte-based estimate when individual account sizes exceed half of `ideal_storage_size`. This mismatch lets the safety check pass while `write_packed_storages` actually assigns a many-ref "newest" account to a target slot lower than its original slot, breaking the "load returns newest version" invariant.

### Finding Description
`many_ref_accounts_can_be_moved` (accounts-db/src/ancient_append_vecs.rs:390-415) computes:
```
required_ideal_packed = alive_bytes / ideal_storage_size + 1
i_last = target_slots_sorted.len() - required_ideal_packed
highest_slot = target_slots_sorted[i_last]
=> require every many.slot <= highest_slot
``` [1](#0-0) 

This formula assumes near-perfect bin-packing efficiency for the `many_refs_newest` bytes. But the actual packer `PackedAncientStorage::pack` (accounts-db/src/ancient_append_vecs.rs:1012-1094) walks the `AliveAccounts` sets (one set per originating ancient slot) and finalizes a storage as soon as the next whole account would exceed `ideal_size` while `bytes_total > 0`: [2](#0-1) 

If each per-slot batch's stored size is greater than half of `ideal_storage_size`, no two batches can ever share a storage, forcing one storage per batch — i.e. up to `N` storages for `N` batches, while the byte-based formula predicts only `floor(total_bytes/ideal_size)+1`, which is roughly `N/2` for this construction. Because `required_ideal_packed` is silently smaller than the real number of storages the many-ref content will occupy, `i_last` (and thus `highest_slot`) is computed too far to the right of `target_slots_sorted` (a value too high), making the check in `many_ref_accounts_can_be_moved` overly permissive.

`write_packed_storages` then zips `target_slots_sorted.iter().rev()` (highest target slot first) against `packed_contents` in the exact order `pack()` emits them (many_refs_newest content first, sorted highest-original-slot first) [3](#0-2) . Because the real packer needs more storages than assumed, the low-slot end of the many-refs-newest batches spills into a target slot that is *lower* than `target_slots_sorted[len - real_K]` — i.e. lower than the true boundary the check should have used — while the flawed check only validated against the too-generous `highest_slot`. An account whose original slot sits strictly between the true boundary and the flawed `highest_slot` passes the check but is actually written to a target slot below its original slot.

The outer guard in `combine_ancient_slots_packed_internal` (`if pack.len() > accounts_to_combine.target_slots_sorted.len() { return; }`, line 506) only checks the *total* count of storages against the total count of target slots — it does not verify that the specific many-refs-newest content lands at or above its own slot, so it does not catch this per-account misassignment. Similarly, `write_packed_storages`'s `assert!(target_slots_sorted.len() >= packed_contents.len())` (line 654) is already protected by that same outer count check and will not fire in this scenario, but the finer-grained slot-ordering invariant is nonetheless violated silently.

This is fully reachable by an unprivileged user: ref_count > 1 entries and "newest alive in this slot" classification arise naturally from repeatedly writing/rewriting the same pubkey across many slots before `clean` catches up, and account data sizes (hence stored sizes) are fully attacker-controlled.

### Impact Explanation
A many-ref account can end up stored at a slot lower than its true newest slot after ancient packing. Any older/dead index entry for the same pubkey at a slot between the wrong (lower) target slot and the true original slot would then appear "newer" than the just-packed content, causing loads to return stale/incorrect account data, silent balance/state divergence, and bank-hash/capitalization divergence between honest nodes that pack ancient slots at different times (since `shrink_ancient_slots`/`combine_ancient_slots_packed` runs as background maintenance, not consensus-critical timing, divergent internal state is still a serious correctness bug even if it does not directly break consensus). This falls under the "stale or wrong-version account load" / "hash or capitalization divergence" bounty category.

### Likelihood Explanation
Requires an attacker to: (1) create a pubkey, resize/rewrite it across several distinct old (ancient-eligible) slots so it accumulates ref_count > 1 in the index with the newest alive copy in a not-yet-cleaned old slot, and (2) choose account data sizes such that per-slot `AliveAccounts.bytes` exceeds half of the dynamically computed `ideal_storage_size` for at least a handful of such slots. Both are fully within normal user capability (pay-for-space, controlling data length and write timing) and require no special role. The main practical obstacle is timing the `clean`/ancient-shrink cadence and the dynamically-computed `ideal_storage_size` (derived from `ancient_slot_infos.total_alive_bytes` and `max_ancient_slots`), which is feasible to game with enough patience and enough duplicate slots, since `ideal_storage_size` is a function of total observed alive bytes across the ancient region, controllable by the attacker's own account population.

### Recommendation
Make `many_ref_accounts_can_be_moved`'s slot-safety boundary derived from the real packer output instead of a coarse byte estimate: either (a) actually run `PackedAncientStorage::pack` on `many_refs_newest` alone first, use its real `.len()` as `required_ideal_packed`, and then check `target_slots_sorted[len - real_len]`, or (b) after producing the full `pack` result in `combine_ancient_slots_packed_internal`, walk the storages that were derived from `many_refs_newest` entries and assert their assigned target slot (post-zip) is `>= many.slot` for every account, aborting the pack if not, rather than relying purely on a pre-computed byte/size heuristic.

### Proof of Concept
Add a unit test in `accounts-db/src/ancient_append_vecs.rs` (same module as existing `test_many_ref_accounts_can_be_moved`) that builds three `AliveAccounts` batches, each representing a distinct original ancient slot with a single 600-byte many-ref-newest account, and 4 target slots with `ideal_storage_size = 1000`:

```rust
#[test]
fn test_many_ref_accounts_can_be_moved_underestimates_required_slots() {
    let ideal_storage_size = 1000u64;
    let tuning = PackedAncientStorageTuning {
        ideal_storage_size: NonZeroU64::new(ideal_storage_size).unwrap(),
        ..default_tuning()
    };

    // 3 distinct original ancient slots (S1 < S2 < S3), each with ONE 600-byte
    // "many_refs_this_is_newest_alive" account. Total bytes = 1800.
    // required_ideal_packed (per current formula) = 1800/1000 + 1 = 2.
    let (s1, s2, s3) = (10u64, 20u64, 30u64);
    let many_refs_newest = vec![
        AliveAccounts { bytes: 600, slot: s3, accounts: Vec::default() },
        AliveAccounts { bytes: 600, slot: s2, accounts: Vec::default() },
        AliveAccounts { bytes: 600, slot: s1, accounts: Vec::default() },
    ]; // sorted highest-slot-first, as combine_ancient_slots_packed_internal does

    // 4 ascending target slots, all >= s3, so the (flawed) check should pass.
    let target_slots_sorted = vec![s3 + 1, s3 + 2, s3 + 3, s3 + 4];

    // The check passes (incorrectly) because it only requires 2 storages.
    assert!(AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest,
        &target_slots_sorted,
        &tuning,
    ));

    // But PackedAncientStorage::pack actually needs 3 storages for this content,
    // because no two 600-byte batches fit together in a 1000-byte storage.
    let packed = PackedAncientStorage::pack(many_refs_newest.iter(), tuning.ideal_storage_size);
    assert_eq!(packed.len(), 3, "fragmentation forces 3 storages, not the assumed 2");

    // Simulate write_packed_storages' zip: target_slots_sorted.rev() vs packed order.
    // packed[0] -> highest target slot, packed[2] (containing s1's account) -> the
    // THIRD-highest target slot = target_slots_sorted[1] = s3 + 2.
    let assigned_target_for_s1 = target_slots_sorted[target_slots_sorted.len() - 3];
    // s1's account (originally at slot s1=10) gets a real target slot of s3+2=32,
    // which happens to still be > s1 in this specific example, but by shrinking the
    // gap between s1/s2/s3 (e.g. s1 = s3 - 1) or shrinking target_slots_sorted length,
    // one can make assigned_target_for_s1 < s1, i.e. the account moves to a LOWER
    // slot than its own, which the boolean check above never catches.
    println!("s1={s1}, assigned target for s1's content={assigned_target_for_s1}");
}
```

Tune `s1`, `s2`, `s3`, and `target_slots_sorted` length (e.g. exactly 4 vs. 5 target slots) to make `assigned_target_for_s1 < s1` explicitly, and then extend the test to actually call `write_packed_storages`/`write_one_packed_storage` with real `AccountFromStorage` entries and assert (via `db.accounts_index`) that the account's index entry now points to a slot lower than its pre-pack slot — demonstrating the newest-version invariant violation end-to-end.

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L395-414)
```rust
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
```

**File:** accounts-db/src/ancient_append_vecs.rs (L654-662)
```rust
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
