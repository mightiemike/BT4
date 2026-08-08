### Title
Bin-packing miscalculation in `many_ref_accounts_can_be_moved` allows `PackedAncientStorage::pack` to assign multi-ref "newest" accounts to a target slot lower than their origin slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`AccountsDb::many_ref_accounts_can_be_moved` decides whether the multi-ref "newest alive" accounts can safely be repacked into the reserved highest target slots by estimating the number of packed storages they will occupy purely from `total_bytes / ideal_storage_size`. `PackedAncientStorage::pack`, however, uses a sequential/"next-fit"-style greedy packer that can require substantially more output storages than this volume estimate when account sizes are adversarially chosen and ordered, which is fully attacker-controlled. If this happens, `write_packed_storages` (which pairs pack outputs in order with `target_slots_sorted.iter().rev()`) can place the tail of the multi-ref "newest" data into a lower target slot than `many_ref_accounts_can_be_moved` verified was safe, potentially below the account's true origin slot.

### Finding Description
`combine_ancient_slots_packed_internal` (`accounts-db/src/ancient_append_vecs.rs:417-518`) collects `many_refs_newest` (multi-ref accounts where the current slot is the highest alive slot for that pubkey) and verifies via `many_ref_accounts_can_be_moved` (`ancient_append_vecs.rs:390-415`) that these accounts can be safely moved into `target_slots_sorted` without violating "must move to a slot >= current slot": [1](#0-0) 

The check computes `required_ideal_packed = alive_bytes / ideal_storage_size + 1` and asserts that the lowest of the top `required_ideal_packed` target slots is `>=` every many-ref account's slot. This assumes exactly `required_ideal_packed` output chunks from `PackedAncientStorage::pack` will hold all `many_refs_newest` bytes.

`PackedAncientStorage::pack` (`ancient_append_vecs.rs:1009-1094`) is a sequential greedy ("next-fit"-like) packer: it walks the concatenated account lists in order (`many_refs_newest` first, then `one_ref` accounts), filling the current output storage until adding the next whole account would exceed `ideal_size`, then starts a new storage. This algorithm's worst-case storage count can exceed `ceil(total_bytes / ideal_size)` (the classic next-fit bin-packing worst case, e.g. alternating "just over half capacity" and small items can force close to double the storages required by volume alone). Since the attacker fully controls account data sizes and the on-disk write order within a slot (accounts are written by normal user transactions and later scanned in that exact order by `shrink_collect`), the attacker can engineer the `many_refs_newest` account set to overflow past the `required_ideal_packed` chunks assumed safe by `many_ref_accounts_can_be_moved`.

`write_packed_storages` (`ancient_append_vecs.rs:646-701`) pairs `pack()`'s output list *in original order* with `target_slots_sorted.iter().rev()` (highest slot first). If `many_refs_newest` data spills into more chunks than `required_ideal_packed`, the overflow chunk(s) get paired with target slots further down the descending list — i.e., lower target slots than `many_ref_accounts_can_be_moved` verified. The only remaining guard, `if pack.len() > accounts_to_combine.target_slots_sorted.len() { return; }` (`ancient_append_vecs.rs:506-509`), only checks the *total* pack length against *total* available target slots — it does not re-validate that the multi-ref-newest portion specifically still lands at or above its origin slot.

Neither `write_ancient_accounts` (`ancient_append_vecs.rs:543-579`) nor `store_accounts_for_squash`/index update performs any assertion that the account is being written to a slot `>=` its prior slot. So if the fragmentation-driven miscalculation occurs, an account whose "newest" instance was at slot X can be rewritten into a packed storage at a slot below X, while an older (`many_refs_old_alive`) instance of the same pubkey remains at some slot between the new (too-low) target slot and X. The account index's slot-list ordering (highest slot = current version) would then expose the stale older instance as the account's current state — a silent state-resurrection bug, purely from the layout/packing operation, violating the stated invariant that "ancient packing never changes observable account state."

### Impact Explanation
This breaks the observable-state invariant of ancient storage compaction: an account version transiently becomes the wrong (older/stale) one because the physical byte-level layout, not any observable logical event, determined which version was "newest" per the slot list. This matches Agave's bounty category of silent stale/wrong-version account loads and honest-node hash/capitalization divergence (since accounts hash calculations and validator replay would derive from the corrupted slot ordering after this repacking runs).

### Likelihood Explanation
Reachability requires: (1) an unprivileged user rewriting the same pubkey across many slots spanning more than one epoch so it becomes multi-ref, ancient, and reaches the "newest alive" bucket; (2) the accumulated bytes and account-size distribution for that pubkey set (and possibly interspersed pubkeys within the same ancient slots, since `shrink_collect` order reflects on-disk append order) to trigger a next-fit worst-case bin-packing pattern relative to `ideal_storage_size`; (3) `shrink_ancient_slots`/`combine_ancient_slots_packed` running over the affected slots. All three preconditions are within the attacker's control (data sizes, write order/frequency, waiting for ancient-slot eligibility) with no special permissions. However, precisely engineering the exact byte-level next-fit worst case against production `ideal_storage_size` values (dynamically computed from total alive ancient bytes) requires careful crafting and is nontrivial to hit reliably in a live cluster, though fully reproducible in a unit/integration test with a fixed `ideal_storage_size`.

### Recommendation
Make `many_ref_accounts_can_be_moved`'s safety check independent of the specific bin-packing algorithm's efficiency, e.g., by having `PackedAncientStorage::pack` actually run (or a dry-run be performed) prior to the "can be moved" decision, and by validating the exact target slot assigned to each `many_refs_newest` chunk (after `write_packed_storages` zips slots to pack output) against the origin slot of every account in that chunk before writing, aborting/falling back to `write_ancient_accounts_to_same_slot_multiple_refs` for any such account whose computed target slot is `<` its current slot instead of assuming the byte-volume estimate is a valid upper bound on storages consumed.

### Proof of Concept
Rust unit test plan for `accounts-db/src/ancient_append_vecs.rs` test module:
```rust
#[test]
fn test_pack_ancient_storages_next_fit_worst_case_violates_slot_order() {
    // Craft a `many_refs_newest` account set and additional one_ref sets such that:
    // - ideal_storage_size = ID (fixed, e.g. 10_000)
    // - many_refs_newest for slot S contains alternating accounts sized
    //   slightly over ID/2 and a small trailing account, chosen so that
    //   PackedAncientStorage::pack's next-fit greedy loop produces MORE
    //   chunks than `required_ideal_packed = bytes/ID + 1` computed by
    //   many_ref_accounts_can_be_moved.
    // - target_slots_sorted contains exactly `required_ideal_packed` slots all >= S,
    //   satisfying `many_ref_accounts_can_be_moved` (returns true).

    let ideal_size = NonZeroU64::new(10_000).unwrap();
    let many_refs_newest = vec![/* AliveAccounts crafted per above */];
    let target_slots_sorted = vec![/* slots all >= S, count == required_ideal_packed */];

    let tuning = PackedAncientStorageTuning { ideal_storage_size: ideal_size, ..default_tuning() };
    assert!(AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest, &target_slots_sorted, &tuning
    )); // passes the safety check

    let pack = PackedAncientStorage::pack(many_refs_newest.iter(), ideal_size);

    // Assert the actual number of chunks produced for many_refs_newest data
    // exceeds required_ideal_packed, proving the estimate was wrong:
    let many_refs_newest_chunks = pack.iter().take_while(|p| /* contains only slot S data */ true).count();
    assert!(many_refs_newest_chunks > required_ideal_packed);

    // Simulate write_packed_storages' zip-with-rev(target_slots_sorted) pairing
    // and assert that the overflow chunk maps to a target slot < S,
    // which is the invariant violation.
    let assigned_slot_for_overflow_chunk = target_slots_sorted
        .iter()
        .rev()
        .nth(many_refs_newest_chunks - 1) // overflow index
        .unwrap();
    assert!(*assigned_slot_for_overflow_chunk < S, "account moved to slot lower than its origin slot!");
}
```
Additionally, a broader invariant/fuzz test should: build a full `AccountsDb`, create multi-ref accounts spanning several ancient slots with adversarial data-size sequences, call `combine_ancient_slots_packed`, then compare the full `pubkey -> (highest alive slot, data, lamports)` map before and after, asserting equality — expected to fail once a next-fit worst-case ordering is found.

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
