### Title
Ancient-slot squashing silently drops carry-forward tombstones for zero-lamport single-ref accounts guarded only by a `debug_assert!` — ([File: accounts-db/src/ancient_append_vecs.rs])

### Summary
The bug pattern in the external report is: a restriction that is correctly enforced on the "normal", per-item code path (only perpetually-locked tokens may carry delegation power) is silently bypassed by a bulk/"move all" code path that has no equivalent enforcement. The analogous defect in `agave`'s `AccountsDb` is in the ancient append-vec squashing ("combine ancient slots") pipeline: the per-slot `shrink_collect`/`load_accounts_index_for_shrink` path correctly computes `tombstones_to_carry_forward` for zero-lamport single-ref accounts that are *not yet* purgeable (i.e. newer than `latest_full_snapshot_slot`), but the bulk ancient-squash *write* path (`write_packed_storages` → `PackedAncientStorage::pack` → `write_one_packed_storage`) never looks at or writes `tombstones_to_carry_forward` at all. The only thing standing between this and silent data loss is a `debug_assert!`, which compiles to a no-op in release builds.

### Finding Description
`load_accounts_index_for_shrink` (used by every `shrink_collect` call, including the one used for ancient combining) puts a zero-lamport single-ref account into `tombstones_to_carry_forward` instead of dropping it whenever `can_purge_zero_lamport_single_ref_after_shrink(slot)` is `false`, i.e. when the slot is *newer* than `latest_full_snapshot_slot`: [1](#0-0) 

That tombstone-carry-forward mechanism exists specifically so that an incremental snapshot can still see and propagate a zero-lamport account's deletion (documented at `filter_zero_lamport_clean_for_incremental_snapshots`): [2](#0-1) 

The normal (non-ancient) shrink write path correctly rewrites `tombstones_to_carry_forward` back to storage. The ancient-squash "bulk mover", however, only packs `alive_accounts` (`one_ref` and `many_refs_this_is_newest_alive`) — it never references `tombstones_to_carry_forward`: [3](#0-2) [4](#0-3) 

The only safeguard is a `debug_assert!` in `finish_combine_ancient_slots_packed_internal`, which fires only in debug builds and is compiled out entirely in release/production validator binaries: [5](#0-4) 

The code comment itself acknowledges the danger: "The squash write path has no tombstone handling, so a non-empty list here would be silently lost." This mirrors the report's bug class exactly — a restriction ("only carry the tombstone forward if not yet purgeable, to keep the incremental snapshot correct") that is properly checked in one code path is completely unenforced in a bulk-processing sibling path, relying only on an invariant assumption ("ancient squash only runs on slots far older than latest full snapshot") that is not actually validated at the point of use.

### Impact Explanation
If the invariant "ancient slots are always older than `latest_full_snapshot_slot`" is violated (e.g., short/custom epoch schedules, clusters where the ancient-append-vec age threshold is smaller than the full-snapshot interval, or before the first full snapshot has been taken while `combine_ancient_slots_packed` is nonetheless invoked), a zero-lamport single-ref account's tombstone byte-record is silently dropped from the rewritten ancient storage in release builds. Since the account is also removed from the accounts index by `remove_zero_lamport_single_ref_accounts_after_shrink`/`shrink_collect`, there is no other place recording that this pubkey went to zero. An incremental snapshot taken afterward would then not see the deletion and would omit propagating it, producing a mismatch between the storage-derived state and what a node that replayed from the (now-missing) tombstone would compute — an honest-node snapshot-vs-replay divergence in per-account state, matching the "silent balance change / snapshot-vs-replay mismatch" impact class.

### Likelihood Explanation
Likelihood is Low/Medium: the guarding invariant ("ancient slots > 1 epoch old are always older than the latest full snapshot") holds under typical mainnet/testnet operation, but is not enforced by any runtime check, only a debug-only assertion. It can plausibly be violated on clusters with unusual `EpochSchedule`/snapshot-interval configurations, or transiently before the first full snapshot completes, both of which are configuration states rather than attacker-controlled inputs, so likelihood of accidental trigger under non-default configs is realistic while direct exploitation is not.

### Recommendation
Add a real runtime check (not `debug_assert!`) in `finish_combine_ancient_slots_packed_internal` / `write_packed_storages` that either (a) explicitly forwards `tombstones_to_carry_forward` into the packed ancient storage the same way the normal shrink write path does, or (b) refuses to squash a slot whose `shrink_collect` returned non-empty `tombstones_to_carry_forward`, deferring it to normal shrink instead of ancient combining. This removes the reliance on an unchecked invariant and prevents any release-build path from silently discarding zero-lamport tombstone information needed for incremental snapshot correctness.

### Proof of Concept
1. Configure a cluster/test with an `EpochSchedule` whose `slots_per_epoch` is small enough (or a validator that has not yet completed its first full snapshot) so that a slot becomes eligible for `combine_ancient_slots_packed`/`combine_ancient_slots_packed_internal` while still being newer than `latest_full_snapshot_slot` (or while `latest_full_snapshot_slot` is such that `can_purge_zero_lamport_single_ref_after_shrink(slot)` returns `false` for that ancient slot).
2. Store a zero-lamport account that becomes single-ref in that ancient-eligible slot (as in `test_shrink_converts_zero_lamport_single_ref_account_to_tombstone`, see `accounts-db/src/accounts_db/tests/impl.rs:1406-1502`, but applied to an ancient-old slot instead of a normal shrink candidate).
3. Trigger `combine_ancient_slots_packed` for that slot in a release build (`debug_assertions` disabled).
4. Observe: `shrink_collect`/`load_accounts_index_for_shrink` computes a non-empty `tombstones_to_carry_forward` for the account, but `write_packed_storages`/`PackedAncientStorage::pack` never writes it, and `finish_combine_ancient_slots_packed_internal`'s `debug_assert!` is compiled out — the tombstone bytes are gone from the resulting ancient storage with no error, panic, or metric raised.

### Citations

**File:** accounts-db/src/accounts_db.rs (L2292-2311)
```rust
    /// During clean, some zero-lamport accounts that are marked for purge should *not* actually
    /// get purged.  Filter out those accounts here by removing them from 'candidates'.
    /// Candidates may contain entries with empty slots list in CleaningInfo.
    /// The function removes such entries from 'candidates'.
    ///
    /// When using incremental snapshots, do not purge zero-lamport accounts if the slot is higher
    /// than the latest full snapshot slot.  This is to protect against the following scenario:
    ///
    ///   ```text
    ///   A full snapshot is taken, including account 'alpha' with a non-zero balance.  In a later slot,
    ///   alpha's lamports go to zero.  Eventually, cleaning runs.  Without this change,
    ///   alpha would be cleaned up and removed completely. Finally, an incremental snapshot is taken.
    ///
    ///   Later, the incremental and full snapshots are used to rebuild the bank and accounts
    ///   database (e.x. if the node restarts).  The full snapshot _does_ contain alpha
    ///   and its balance is non-zero.  However, since alpha was cleaned up in a slot after the full
    ///   snapshot slot (due to having zero lamports), the incremental snapshot would not contain alpha.
    ///   Thus, the accounts database will contain the old, incorrect info for alpha with a non-zero
    ///   balance.  Very bad!
    ///   ```
```

**File:** accounts-db/src/accounts_db.rs (L2429-2438)
```rust
                    if stored_account.is_zero_lamport() && ref_count == 1 {
                        // The lone instance of a zero-lamport account. A load of a zero-lamport
                        // account already reports "not found", so dropping its index entry is safe.
                        zero_lamport_single_ref_pubkeys.push(pubkey);
                        if !can_purge_zero_lamport_single_ref {
                            // Newer than the latest full snapshot: keep the bytes in storage as a
                            // tombstone so an incremental snapshot can still propagate the deletion,
                            // rather than dropping it.
                            tombstones.push(*stored_account);
                        }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L494-503)
```rust
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
```

**File:** accounts-db/src/ancient_append_vecs.rs (L730-742)
```rust
        let mut dropped_roots = Vec::with_capacity(accounts_to_combine.accounts_to_combine.len());
        for shrink_collect in accounts_to_combine.accounts_to_combine {
            let slot = shrink_collect.slot;

            // Ancient squash only runs on slots far older than the latest full snapshot, where
            // tombstones are purgeable and `shrink_collect` drops them rather than carrying them
            // forward. The squash write path has no tombstone handling, so a non-empty list here
            // would be silently lost; assert the invariant at the point that loss would occur.
            debug_assert!(
                shrink_collect.tombstones_to_carry_forward.is_empty(),
                "ancient squash reached a carry-forward tombstone at slot {slot}",
            );

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
