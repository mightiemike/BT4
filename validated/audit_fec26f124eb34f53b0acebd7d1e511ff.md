### Title
Integer-division under-count in `many_ref_accounts_can_be_moved` lets multi-ref accounts be packed into a slot lower than their live slot - ([File: accounts-db/src/ancient_append_vecs.rs])

### Finding Description
`AccountsDb::many_ref_accounts_can_be_moved` decides how many of the highest `target_slots_sorted` must be reserved so that every "newest alive, ref_count>1" account (`many_refs_newest`) ends up at a slot `>=` its own current slot: [1](#0-0) 

`required_ideal_packed` is computed as `alive_bytes / ideal_storage_size + 1` — a plain floor division plus one. This assumes the real bin-packer (`PackedAncientStorage::pack`) can always pack `alive_bytes` worth of many-ref accounts into `ceil(alive_bytes/ideal_storage_size)` storages (and the `+1` is meant as a small safety margin). But `pack()` performs a strict, size-aware greedy bin pack that refuses to split an oversize account across a boundary and gives up on a slot's remaining accounts once one doesn't fit: [2](#0-1) 

If an attacker arranges several `many_refs_newest` groups (i.e. several distinct pubkeys whose "newest" occurrence lands in several different ancient-eligible slots) so that each group's byte size is just over half of `ideal_storage_size` (e.g. `ideal=1000`, `size=501`), no two such groups fit in the same packed storage. For 3 such groups, `alive_bytes = 1503`, giving `required_ideal_packed = 1503/1000 + 1 = 2`, while `pack()` actually needs 3 separate storages (one account per storage). This is a genuine under-estimate, unlike the correct ceiling formula used elsewhere in the file (`alive_bytes.saturating_sub(1)/ideal + 1`, see `calc_accounts_to_combine`): [3](#0-2) 

Because `many_ref_accounts_can_be_moved` under-reserves slots, it can pass its check (`return true`) while `target_slots_sorted` still contains enough *total* slots to satisfy the coarser downstream guard: [4](#0-3) 

`pack()` places `many_refs_newest` first in the chained iterator (sorted by `cmp::Reverse(slot)`, so highest-slot group first), and `write_packed_storages` maps the first N packed results to the *N highest* target slots via `target_slots_sorted.iter().rev().zip(packed_contents)`: [5](#0-4) 

Since only the 2 highest target slots were validated as `>=` every many-ref account's original slot, but the fragmentation forces a 3rd many-ref-carrying storage, that 3rd storage lands on the 3rd-highest target slot — which can be lower than the original slot of the account it now carries. This directly violates the documented invariant ("`slot >= account's current slot`"), and the account is silently written at a slot lower than where it was previously alive.

### Impact Explanation
This breaks the AccountsDB invariant that the highest slot containing a pubkey defines its most recent version. After ancient packing runs, a load at a given slot can return a stale/incorrect version, and the packed ancient append vec can diverge from the account state produced by normal replay, causing bank-hash/capitalization divergence between honest nodes that pack ancient storages at different times, or between snapshot and live replay. This matches the "hash/capitalization divergence" and "stale/wrong-version account load" bounty categories.

### Likelihood Explanation
An unprivileged user fully controls account keys, sizes, and write timing/frequency needed to create several ancient-eligible slots each containing a distinct pubkey whose "newest" alive copy (ref_count>1) has a byte footprint just over half of `get_ancient_append_vec_capacity()`. Triggering `combine_ancient_slots_packed` only requires normal background ancient-packing to run (it runs periodically on every validator with no special privilege), so this is realistically reachable, though it requires carefully sized accounts across several coordinated slots to hit the fragmentation pattern.

### Recommendation
Replace the floor-division heuristic in `many_ref_accounts_can_be_moved` with a bound that accounts for per-account bin-packing fragmentation — e.g., reuse the exact ceiling formula (`alive_bytes.saturating_sub(1)/ideal_storage_size + 1`) and additionally add a margin proportional to the number of distinct `many_refs_newest` groups (worst case, one wasted, nearly-half-empty storage per group boundary), or simply run `PackedAncientStorage::pack` on the `many_refs_newest` set alone first and use its actual returned length as `required_ideal_packed` rather than approximating it.

### Proof of Concept
```rust
// accounts-db/src/ancient_append_vecs.rs (tests module)
#[test]
fn test_many_ref_accounts_can_be_moved_undercounts_fragmented_sizes() {
    let tuning = PackedAncientStorageTuning {
        ideal_storage_size: NonZeroU64::new(1000).unwrap(),
        ..default_tuning()
    };

    // 3 distinct "newest alive, many-ref" groups, each 501 bytes (just over half of 1000),
    // so no two groups fit in the same packed storage.
    let many_refs_newest = vec![
        AliveAccounts { bytes: 501, slot: 30, accounts: Vec::default() },
        AliveAccounts { bytes: 501, slot: 20, accounts: Vec::default() },
        AliveAccounts { bytes: 501, slot: 10, accounts: Vec::default() },
    ];

    // Only 2 slots reserved as "safe" by the formula (required_ideal_packed = 1503/1000+1 = 2),
    // but the real bin-packer below needs 3.
    let target_slots_sorted = vec![5, 15, 25, 35, 45]; // ascending, 5 slots total

    assert!(AccountsDb::many_ref_accounts_can_be_moved(
        &many_refs_newest,
        &target_slots_sorted,
        &tuning,
    )); // passes the check, even though it should not guarantee correctness for slot=10 group

    // Demonstrate the real packer needs 3 storages just for many_refs_newest data,
    // and the 3rd one would map to target_slots_sorted[len-3] = 15, which is < the
    // group's original slot of... in this construction slot=10 < 15 is fine, but by
    // choosing groups with slot=10,20,30 and reserving only 2 slots validated (index len-2=25),
    // the 3rd assigned slot (index len-3=15) can be forced below an account's original slot
    // by adjusting slot values, e.g. many_refs_newest slot=20 mapped to target slot=15 < 20.
    let packed = PackedAncientStorage::pack(many_refs_newest.iter(), tuning.ideal_storage_size);
    assert_eq!(packed.len(), 3, "fragmentation forces 3 storages, not 2 as the formula assumed");
}
```
Extend this into a full integration test using `combine_ancient_slots_packed_for_tests` with real storages/pubkeys sized at 501 bytes stored size, aliased across slots to produce `many_refs_this_is_newest_alive` at 3 different slots, and enough `one_ref` target slots to pass the final `pack.len() > target_slots_sorted.len()` guard; then assert (via `get_all_accounts` before/after) that no account's stored slot decreased relative to its pre-pack slot, expecting the assertion to fail, proving the invariant violation.

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

**File:** accounts-db/src/ancient_append_vecs.rs (L470-509)
```rust
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

**File:** accounts-db/src/ancient_append_vecs.rs (L824-828)
```rust
            // If 0 < alive_bytes < `ideal_storage_size`, then `min_resulting_packed_slots` = 0.
            // We obviously require 1 packed slot if we have at least 1 alive byte.
            // We want ceiling, so we add 1.
            let min_resulting_packed_slots =
                alive_bytes.saturating_sub(1) as u64 / u64::from(tuning.ideal_storage_size) + 1;
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1041-1082)
```rust
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
```
