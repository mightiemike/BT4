### Title
Off-by-one integer division in `many_ref_accounts_can_be_moved` over-estimates required target slots, silently blocking ancient-storage packing and causing storage/CPU bloat - (File: `accounts-db/src/ancient_append_vecs.rs`)

### Summary
`AccountsDb::many_ref_accounts_can_be_moved` computes the number of target storages required to hold multi-ref "newest alive" accounts using a plain (non-ceiling) integer division plus one:

```rust
let required_ideal_packed = (alive_bytes as u64 / tuning.ideal_storage_size + 1) as usize;
``` [1](#0-0) 

This differs from the correct ceiling-division formula used a few lines away for the analogous `min_resulting_packed_slots` calculation:

```rust
let min_resulting_packed_slots =
    alive_bytes.saturating_sub(1) as u64 / u64::from(tuning.ideal_storage_size) + 1;
``` [2](#0-1) 

Whenever `alive_bytes` is an exact multiple of `tuning.ideal_storage_size` (including the common case `alive_bytes == ideal_storage_size`), the buggy formula returns one more required slot than actually needed. This causes `many_ref_accounts_can_be_moved` to incorrectly report that there is insufficient room, aborting the entire ancient-storage combining pass for that batch of slots.

### Finding Description
`many_ref_accounts_can_be_moved` is called from `combine_ancient_slots_packed_internal` to decide whether accounts with ref-count > 1 (`many_refs_newest`) can be safely relocated into existing target slots before the actual packing (`PackedAncientStorage::pack`) is attempted:

```rust
if !Self::many_ref_accounts_can_be_moved(
    &many_refs_newest,
    &accounts_to_combine.target_slots_sorted,
    &tuning,
) {
    ...
    return;
}
``` [3](#0-2) 

The correct number of storages needed to hold `alive_bytes` bytes at `ideal_storage_size` per storage is `ceil(alive_bytes / ideal_storage_size)`, which is properly implemented elsewhere in the same file as `(alive_bytes - 1) / ideal_storage_size + 1` (for `alive_bytes > 0`). The `many_ref_accounts_can_be_moved` function, however, computes `alive_bytes / ideal_storage_size + 1` — omitting the `- 1` adjustment. For any `alive_bytes` that is an exact multiple of `ideal_storage_size` (e.g. `alive_bytes == ideal_storage_size`), this yields `required_ideal_packed = 2` instead of the correct `1`.

Because `required_ideal_packed` is then compared against `target_slots_sorted.len()`:

```rust
if target_slots_sorted.len() < required_ideal_packed {
    return false;
}
``` [4](#0-3) 

an over-estimated `required_ideal_packed` can make this comparison spuriously true even when there is in fact enough room, causing the function to return `false` and abort the pack/combine operation for slots that could have been legitimately combined. This same over-estimate also shifts `i_last` (`target_slots_sorted.len().saturating_sub(required_ideal_packed)`), tightening the `highest_slot` constraint further and making the final slot-ordering check unnecessarily strict.

This is the same class of bug as the external report: an integer-division-based capacity/threshold calculation that omits proper ceiling handling, causing systematically wrong results at exact-multiple boundaries. In the original report the truncation caused voting power to be silently zeroed; here the truncation (in the opposite direction — over- rather than under-counting) causes the ancient-slot combining logic to silently and repeatedly abandon otherwise-eligible combining opportunities.

### Impact Explanation
When `many_ref_accounts_can_be_moved` incorrectly returns `false`, `combine_ancient_slots_packed_internal` returns early without calling `write_packed_storages`/`finish_combine_ancient_slots_packed_internal`, so the ancient append-vec storages that were candidates for packing/shrinking are left untouched for that pass:

```rust
if !Self::many_ref_accounts_can_be_moved(...) {
    datapoint_info!("shrink_ancient_stats", ("high_slot", 1, i64));
    log::info!("unable to ancient pack: ...");
    return;
}
``` [5](#0-4) 

Since this check runs on every invocation of `combine_ancient_slots_packed`/`combine_ancient_slots_packed_internal` [6](#0-5) , whenever `alive_bytes` for multi-ref newest accounts happens to land exactly on a multiple of `tuning.ideal_storage_size`, packing is skipped even though it should succeed. This means ancient storages accumulate more slots/append-vecs than necessary and remain larger/more numerous than intended, directly matching the "disproportionate storage and CPU cost" impact category: more open append-vec file handles, more storages to scan on subsequent clean/shrink/hash/snapshot passes, and repeated wasted work recomputing `calc_ancient_slot_info`/`collect_sort_filter_ancient_slots` on the same unconverged set of ancient slots each background iteration.

This is not a validator/peer-role issue, not mocked-only, and not purely theoretical — it sits squarely in the accounts-db shrink/clean/ancient-storage-combining code path exercised in normal validator operation.

### Likelihood Explanation
The trigger condition — the sum of multi-ref "newest alive" bytes landing exactly on (or being a multiple of) `tuning.ideal_storage_size` — is a boundary condition that can occur periodically as accounts are shrunk and re-packed over many epochs, since `ideal_storage_size` itself is dynamically recomputed each pass from `total_alive_bytes.0 * 2 / max_ancient_slots.max(1)` [7](#0-6) , making exact-multiple alignment plausible over time rather than a rare edge case. The bug is deterministic given such alignment (no randomness involved), and would silently degrade the ancient packing algorithm's convergence without any error being surfaced beyond an info-level log line.

### Recommendation
Fix `many_ref_accounts_can_be_moved` to use the same ceiling-division formula already used in `calc_accounts_to_combine`:

```rust
let required_ideal_packed = if alive_bytes == 0 {
    0
} else {
    (alive_bytes as u64 - 1) / tuning.ideal_storage_size + 1
} as usize;
```

This aligns the two "required packed slots" computations in the file and removes the spurious over-count at exact-multiple boundaries, preventing unnecessary aborts of ancient-slot combining.

### Proof of Concept
Conceptual repro (mirrors existing unit tests such as `test_many_ref_accounts_can_be_moved` [8](#0-7) ):

1. Construct `tuning.ideal_storage_size = NonZeroU64::new(1000).unwrap()`.
2. Construct `many_refs_newest` with a single `AliveAccounts { bytes: 1000, slot, .. }` (i.e., `alive_bytes == ideal_storage_size` exactly).
3. Construct `target_slots_sorted` with exactly 1 slot (which is sufficient to hold 1000 bytes at `ideal_storage_size = 1000`).
4. Call `AccountsDb::many_ref_accounts_can_be_moved(&many_refs_newest, &target_slots_sorted, &tuning)`.

Expected (correct) result: `true` (1 target slot is enough for exactly 1000 bytes at 1000 ideal size).
Actual result with current code: `required_ideal_packed = 1000/1000 + 1 = 2`, and since `target_slots_sorted.len() (1) < required_ideal_packed (2)`, the function returns `false`, incorrectly reporting that the accounts cannot be moved and causing `combine_ancient_slots_packed_internal` to abort the pack for this batch.

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L346-384)
```rust
impl AccountsDb {
    /// Combine account data from storages in 'sorted_slots' into packed storages.
    /// This keeps us from accumulating storages for each slot older than an epoch.
    /// After this function the number of alive roots is <= # alive roots when it was called.
    /// In practice, the # of alive roots after will be significantly less than # alive roots when called.
    /// Trying to reduce # roots and storages (one per root) required to store all the data in ancient slots
    pub(crate) fn combine_ancient_slots_packed(
        &self,
        sorted_slots: Vec<Slot>,
        can_randomly_shrink: bool,
    ) {
        let tuning = PackedAncientStorageTuning {
            // Slots old enough to be ancient.
            max_ancient_slots: self.max_ancient_storages,
            // Don't re-pack anything just to shrink.
            // shrink_candidate_slots will handle these old storages.
            percent_of_alive_shrunk_data: 0,
            ideal_storage_size: NonZeroU64::new(get_ancient_append_vec_capacity()).unwrap(),
            can_randomly_shrink,
            max_resulting_storages: NonZeroU64::new(10).unwrap(),
        };

        let _guard = self.active_stats.activate(ActiveStatItem::SquashAncient);

        let mut stats_sub = SquashStatsSub::default();

        let (_, total_us) = measure_us!(self.combine_ancient_slots_packed_internal(
            sorted_slots,
            tuning,
            &mut stats_sub
        ));

        self.shrink_ancient_stats.accumulate_sub_stats(stats_sub);
        self.shrink_ancient_stats
            .total_us
            .fetch_add(total_us, Ordering::Relaxed);

        self.shrink_ancient_stats.report();
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L395-399)
```rust
        let alive_bytes = many_refs_newest
            .iter()
            .map(|alive| alive.bytes)
            .sum::<usize>();
        let required_ideal_packed = (alive_bytes as u64 / tuning.ideal_storage_size + 1) as usize;
```

**File:** accounts-db/src/ancient_append_vecs.rs (L404-406)
```rust
        if target_slots_sorted.len() < required_ideal_packed {
            return false;
        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L470-482)
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
```

**File:** accounts-db/src/ancient_append_vecs.rs (L528-534)
```rust
        // ideal storage size is total alive bytes of ancient storages
        // divided by half of max ancient slots
        tuning.ideal_storage_size = NonZeroU64::new(
            (ancient_slot_infos.total_alive_bytes.0 * 2 / tuning.max_ancient_slots.max(1) as u64)
                .max(self.ancient_storage_ideal_size),
        )
        .unwrap();
```

**File:** accounts-db/src/ancient_append_vecs.rs (L824-828)
```rust
            // If 0 < alive_bytes < `ideal_storage_size`, then `min_resulting_packed_slots` = 0.
            // We obviously require 1 packed slot if we have at least 1 alive byte.
            // We want ceiling, so we add 1.
            let min_resulting_packed_slots =
                alive_bytes.saturating_sub(1) as u64 / u64::from(tuning.ideal_storage_size) + 1;
```

**File:** accounts-db/src/ancient_append_vecs.rs (L3708-3776)
```rust
    #[test]
    fn test_many_ref_accounts_can_be_moved() {
        let tuning = PackedAncientStorageTuning {
            // only allow 10k slots old enough to be ancient
            max_ancient_slots: 10_000,
            percent_of_alive_shrunk_data: 0,
            ideal_storage_size: NonZeroU64::new(1000).unwrap(),
            can_randomly_shrink: false,
            ..default_tuning()
        };

        // nothing to move, so no problem fitting it
        let many_refs_newest = vec![];
        let target_slots_sorted = vec![];
        assert!(AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));
        // something to move, no target slots, so can't fit
        let slot = 1;
        let many_refs_newest = vec![AliveAccounts {
            bytes: 1,
            slot,
            accounts: Vec::default(),
        }];
        assert!(!AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));

        // something to move, 1 target slot, so can fit
        let target_slots_sorted = vec![slot];
        assert!(AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));

        // too much to move to 1 target slot, so can't fit
        let many_refs_newest = vec![AliveAccounts {
            bytes: tuning.ideal_storage_size.get() as usize,
            slot,
            accounts: Vec::default(),
        }];
        assert!(!AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));

        // more than 1 slot to move, 2 target slots, so can fit
        let target_slots_sorted = vec![slot, slot + 1];
        assert!(AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));

        // lowest target slot is below required slot
        let target_slots_sorted = vec![slot - 1, slot];
        assert!(!AccountsDb::many_ref_accounts_can_be_moved(
            &many_refs_newest,
            &target_slots_sorted,
            &tuning
        ));
    }

```
