### Title
Ancient-shrink candidate ordering uses unaligned `written_bytes` instead of the aligned value it computes, causing incorrect "bytes saved" prioritization - ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
`AncientSlotInfos::sort_shrink_indexes_by_bytes_saved` explicitly computes an alignment-corrected `aligned_written_bytes` because it knows `alive_bytes` "assumes the accounts are aligned" while the raw `written_bytes` of a storage "may not be aligned for the last account." However, after computing `aligned_written_bytes` and using it only for a sanity check, the actual value returned for sorting purposes falls back to the raw, unaligned `item.written_bytes` in the subtraction. This is the same class of bug as the `split()` rounding bug: two code paths that must agree on a rounded/aligned boundary value diverge, and the checkpoint/consumer logic (`sort_unstable_by`/`amount_shrunk`) silently uses the wrong (un-rounded) value.

### Finding Description
In `accounts-db/src/ancient_append_vecs.rs`: [1](#0-0) 

```rust
fn sort_shrink_indexes_by_bytes_saved(&mut self) {
    self.shrink_indexes.sort_unstable_by(|l, r| {
        let amount_shrunk = |index: &usize| {
            let item = &self.all_infos[*index];
            // alive_bytes assumes the accounts are aligned. `written_bytes` may
            // not be aligned for the last account. Therefore, we need to
            // align it.
            let aligned_written_bytes = u64_align!(item.written_bytes as usize) as u64;
            if aligned_written_bytes < item.alive_bytes {
                // should not happen, but if it does, submit warn log it and continue
                datapoint_warn!(
                    "aligned_written_bytes_less_than_alive_bytes",
                    ("aligned_written_bytes", aligned_written_bytes, i64),
                    ("alive_bytes", item.alive_bytes, i64)
                );
            }
            item.written_bytes.saturating_sub(item.alive_bytes)
        };
        amount_shrunk(r).cmp(&amount_shrunk(l))
    });
}
```

The comment states the developer's intent: `written_bytes` must be aligned before comparing/subtracting against `alive_bytes`, because `alive_bytes` is derived from stored-size calculations that are always aligned (see `AppendVec::calculate_stored_size`/`u64_align!` usage throughout `append_vec.rs`). The code even computes `aligned_written_bytes` for exactly this purpose and uses it in a sanity-check warning. But the value actually returned and used to rank candidates — `item.written_bytes.saturating_sub(item.alive_bytes)` — uses the raw, un-aligned `written_bytes`, not `aligned_written_bytes`. This mirrors the VotingEscrow bug: the "aligned/rounded" quantity is computed but not applied where correctness in the downstream comparison/checkpoint depends on it, so the ranking function it feeds — the ordering used by `filter_ancient_slots`/`choose_storages_to_shrink` (`accounts-db/src/ancient_append_vecs.rs:148-160`) to decide which ancient storages get packed and shrunk first — is quietly wrong whenever a storage's last account leaves it un-aligned to the `u64_align!` boundary. [2](#0-1) 

This selection feeds `combine_ancient_slots_packed`, which is the mechanism used to combine/shrink ancient append vecs and is triggered periodically and automatically by `AccountsBackgroundService`'s `shrink_ancient_slots` path — reachable purely by ordinary account activity (any unprivileged user's transactions creating/aging slots into "ancient" territory), with no special privilege required.

### Impact Explanation
Because the raw (unaligned) `written_bytes` is always `<=` the aligned value, `amount_shrunk` computed here is systematically biased low (by up to the alignment padding, currently 8 bytes per storage, as used by `u64_align!`) relative to what the alive/dead-byte accounting elsewhere in the file assumes. This can invert the intended ordering between two ancient storages whose true "bytes saved" values are close, causing `filter_by_smallest_capacity`/`choose_storages_to_shrink` to prioritize a less-optimal set of ancient storages for combination. The practical consequence is disproportionate/needless storage and CPU cost: sub-optimal ancient storages are repeatedly selected for packing while storages that would free more disk space and I/O are deferred, slowing convergence of the ancient-append-vec packing algorithm and increasing the steady-state disk footprint and re-scan cost of `shrink_ancient_slots` across cleaning cycles.

### Likelihood Explanation
`shrink_ancient_slots` runs automatically and continuously as part of `AccountsBackgroundService`'s normal shrink loop on every validator, and any set of ancient storages whose last stored account is not a multiple of the `u64_align!` boundary (essentially guaranteed for real-world account data sizes) will exercise this code path. No attacker action or special permission is required — it is triggered purely by ordinary usage causing accounts to age into ancient slots.

### Recommendation
Use the already-computed `aligned_written_bytes` (not the raw `item.written_bytes`) in the final subtraction so the "amount shrunk" metric is internally consistent with how `alive_bytes` is computed:

```diff
-            item.written_bytes.saturating_sub(item.alive_bytes)
+            aligned_written_bytes.saturating_sub(item.alive_bytes)
```

### Proof of Concept
1. Create two ancient storages, `A` and `B`, each holding accounts whose last account results in the storage's `written_bytes()` being a few bytes short of an alignment boundary (i.e. not a multiple of the `u64_align!` word size).
2. Compute `alive_bytes` for each storage (as `alive_bytes_after_shrink` does) — this value is always alignment-rounded.
3. Call `sort_shrink_indexes_by_bytes_saved()` and observe that the ordering of `shrink_indexes` is based on `written_bytes - alive_bytes` using the *unaligned* `written_bytes`, differing from the ordering that would result from using `aligned_written_bytes - alive_bytes` (as the existing unit test `test_sort_shrink_indexes_by_bytes_saved` at `accounts-db/src/ancient_append_vecs.rs:3358-3395` demonstrates the comparator's mechanics, but does not assert alignment correctness of the values it compares). [3](#0-2)

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L148-160)
```rust
    /// modify 'self' to contain only the slot infos for the slots that should be combined
    /// (and in this process effectively shrunk)
    fn filter_ancient_slots(
        &mut self,
        tuning: &PackedAncientStorageTuning,
        stats: &ShrinkAncientStats,
    ) {
        // figure out which slots to combine
        // 1. should_shrink: largest bytes saved above some cutoff of ratio
        self.choose_storages_to_shrink(tuning);
        // 2. smallest files so we get the largest number of files to remove
        self.filter_by_smallest_capacity(tuning, stats);
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L162-183)
```rust
    // sort 'shrink_indexes' by most bytes saved, highest to lowest
    fn sort_shrink_indexes_by_bytes_saved(&mut self) {
        self.shrink_indexes.sort_unstable_by(|l, r| {
            let amount_shrunk = |index: &usize| {
                let item = &self.all_infos[*index];
                // alive_bytes assumes the accounts are aligned. `written_bytes` may
                // not be aligned for the last account. Therefore, we need to
                // align it.
                let aligned_written_bytes = u64_align!(item.written_bytes as usize) as u64;
                if aligned_written_bytes < item.alive_bytes {
                    // should not happen, but if it does, submit warn log it and continue
                    datapoint_warn!(
                        "aligned_written_bytes_less_than_alive_bytes",
                        ("aligned_written_bytes", aligned_written_bytes, i64),
                        ("alive_bytes", item.alive_bytes, i64)
                    );
                }
                item.written_bytes.saturating_sub(item.alive_bytes)
            };
            amount_shrunk(r).cmp(&amount_shrunk(l))
        });
    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L3358-3395)
```rust
    #[test]
    fn test_sort_shrink_indexes_by_bytes_saved() {
        let (db, slot1) = create_db_with_storages_and_index(true /*alive*/, 1, None);
        let storage = db.storage.get_slot_storage_entry(slot1).unwrap();
        // ignored
        let slot = 0;

        // info1 is first, equal, last
        for info1_written_bytes in [0, 1, 2] {
            let info1 = SlotInfo {
                storage: storage.clone(),
                slot,
                written_bytes: info1_written_bytes,
                alive_bytes: 0,
                should_shrink: false,
                is_high_slot: false,
            };
            let info2 = SlotInfo {
                storage: storage.clone(),
                slot,
                written_bytes: 2,
                alive_bytes: 1,
                should_shrink: false,
                is_high_slot: false,
            };
            let mut infos = AncientSlotInfos {
                all_infos: vec![info1, info2],
                shrink_indexes: vec![0, 1],
                ..AncientSlotInfos::default()
            };
            infos.sort_shrink_indexes_by_bytes_saved();
            let first = &infos.all_infos[infos.shrink_indexes[0]];
            let second = &infos.all_infos[infos.shrink_indexes[1]];
            let first_capacity = first.written_bytes - first.alive_bytes;
            let second_capacity = second.written_bytes - second.alive_bytes;
            assert!(first_capacity >= second_capacity);
        }
    }
```
