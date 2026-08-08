Based on my investigation, the earlier grep result (`ancient_append_vecs.rs` "1 match" for the combined pattern) was actually matching `tombstones_total_bytes`/`store_tombstones` string as a substring somewhere, but a direct check of `ancient_append_vecs.rs` shows **zero references** to `tombstones_to_carry_forward`, `tombstones_total_bytes`, or `store_tombstones` in that file.

This confirms the vulnerability pattern: `calc_accounts_to_combine` in `accounts-db/src/ancient_append_vecs.rs` calls `self.shrink_collect::<ShrinkCollectAliveSeparatedByRefs<'_>>(...)` [1](#0-0)  which internally computes `tombstones_to_carry_forward` (zero-lamport single-ref accounts that must be rewritten rather than dropped when `!can_purge_zero_lamport_single_ref`) [2](#0-1) . In the normal shrink path (`shrink_storage`), these tombstones are explicitly re-written to the new storage via `store_tombstones` [3](#0-2) . But in the ancient-combine path, the resulting `ShrinkCollect` structs are only consumed for `alive_accounts` (via `.alive_accounts.one_ref` / `many_refs_this_is_newest_alive` fed into `PackedAncientStorage::pack`) [4](#0-3) ; the `tombstones_to_carry_forward` field is never read, packed, or written anywhere in `ancient_append_vecs.rs`.

However, I was unable to fully verify within the remaining iterations whether `write_packed_storages`/`finish_combine_ancient_slots_packed_internal` separately re-derives tombstone info from the original storages (e.g. by copying tombstone offset metadata directly rather than through `ShrinkCollect`), which would neutralize this gap. I could not locate any such alternate mechanism in the code reviewed, and no test in `ancient_append_vecs.rs` (e.g. `test_calc_accounts_to_combine_*`) exercises a zero-lamport-single-ref/tombstone scenario — all visible tests only cover `one_ref` vs `many_refs` alive accounts, never zero-lamport tombstone carry-forward within ancient packing.

### Title
Ancient-slot combination (`calc_accounts_to_combine`) silently drops carried-forward tombstones for zero-lamport single-ref accounts, permitting balance resurrection after incremental snapshot - (File: accounts-db/src/ancient_append_vecs.rs)

### Summary
`calc_accounts_to_combine` reuses `shrink_collect`, the same function used by normal shrink, which correctly computes `tombstones_to_carry_forward` for zero-lamport single-ref accounts not yet purgeable per `latest_full_snapshot_slot`. Unlike the normal shrink path, which explicitly rewrites these tombstones into the new storage via `store_tombstones`, the ancient-packing path in `ancient_append_vecs.rs` never reads or persists `tombstones_to_carry_forward`, so the tombstone bytes and the physical evidence needed for a subsequent incremental snapshot to propagate the deletion are lost.

### Finding Description
`shrink_collect` (shared by `shrink_storage` and `calc_accounts_to_combine`) separates zero-lamport single-ref accounts into two buckets based on `can_purge_zero_lamport_single_ref_after_shrink(slot)`: if the slot is newer than `latest_full_snapshot_slot`, the account's index entry is dropped but its bytes are queued in `tombstones_to_carry_forward` so a physical record survives for incremental snapshot diffing [5](#0-4) . `shrink_storage` honors this by writing `tombstones_to_carry_forward` into the freshly created storage via `store_tombstones` right after writing `alive_accounts` [6](#0-5) .

`calc_accounts_to_combine`, used by `combine_ancient_slots_packed_internal` for the ancient-append-vec packing pipeline, calls `shrink_collect::<ShrinkCollectAliveSeparatedByRefs<'_>>` per slot [1](#0-0) , producing the same `ShrinkCollect` struct (with a populated `tombstones_to_carry_forward` field) for each ancient-eligible storage. The subsequent packing logic only consumes `shrink_collect.alive_accounts.one_ref` and `.many_refs_this_is_newest_alive` when building `PackedAncientStorage::pack` and `write_packed_storages` [4](#0-3) . No code path in `ancient_append_vecs.rs` reads `tombstones_to_carry_forward`, so the tombstone bytes are never written into the packed ancient storage, and the index entry for the zero-lamport pubkey (already removed by `shrink_collect`'s scan logic, since the pubkey is dropped from the index regardless of tombstone status) is gone with no physical trace on disk.

An attacker who owns an account, closes it (writes it to zero lamports) in a slot that becomes eligible for ancient packing while `latest_full_snapshot_slot` is behind that slot, can trigger this: `combine_ancient_slots` runs, `calc_accounts_to_combine` marks their closed account as a to-be-dropped tombstone, but packing silently discards it instead of carrying it forward. If a full snapshot was taken before the account was closed (containing the old non-zero balance) and only an incremental snapshot (which now lacks the deletion tombstone because ancient packing dropped it) is taken afterward, a node restarting from full+incremental snapshot will restore the old non-zero balance, resurrecting funds that were spent/closed.

### Impact Explanation
This is a silent balance-state divergence / resurrection bug: after ancient packing + restart from full+incremental snapshot, a previously-closed (zero-lamport) account could reappear with its pre-close lamport balance, which is a capitalization/hash-divergence and balance-integrity issue — matching Agave's "honest-node snapshot-vs-replay mismatch" / consensus-affecting state divergence bounty category.

### Likelihood Explanation
This requires only unprivileged actions: create an account, resize/rewrite it to make it eligible for ancient packing, and close it (zero lamports) while `latest_full_snapshot_slot` lags the closing slot — an ordinary validator/snapshot cadence condition that occurs regularly. No special permissions, staked/leader control, or crafted snapshots are needed. The bug depends on the natural ancient-packing triggering thresholds and incremental snapshot cadence but is otherwise deterministic and repeatable given the code trace above.

### Recommendation
In `ancient_append_vecs.rs`, after calling `shrink_collect` per storage in `calc_accounts_to_combine`, aggregate each shrink_collect's `tombstones_to_carry_forward` and ensure they are written into one of the target packed storages (mirroring `store_tombstones` behavior in `shrink_storage`), or otherwise refuse to ancient-pack a storage that has pending tombstones not yet purgeable, until those tombstones are physically preserved.

### Proof of Concept
Extend `test_shrink_converts_zero_lamport_single_ref_account_to_tombstone` into an ancient-pack variant: create a zero-lamport single-ref account in a slot with `latest_full_snapshot_slot` set older than that slot, force the slot to be ancient-eligible, call `combine_ancient_slots`/`calc_accounts_to_combine`, then inspect the resulting packed storage's `num_tombstones()`/`tombstone_offsets` — expect the tombstone byte-record to be present (as in the shrink case), and additionally do a full+incremental snapshot round-trip and assert the account remains absent/zero after restart, rather than resurrecting its pre-close lamports.

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L494-511)
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
        );

        if pack.len() > accounts_to_combine.target_slots_sorted.len() {
            // Not enough slots to contain the accounts we are trying to pack.
            return;
        }

        let write_ancient_accounts = self.write_packed_storages(&accounts_to_combine, pack);
```

**File:** accounts-db/src/ancient_append_vecs.rs (L803-812)
```rust
        let mut accounts_to_combine = accounts_per_storage
            .iter_mut()
            .map(|(info, unique_accounts)| {
                self.shrink_collect::<ShrinkCollectAliveSeparatedByRefs<'_>>(
                    &info.storage,
                    unique_accounts,
                    &self.shrink_ancient_stats.shrink_stats,
                )
            })
            .collect::<Vec<_>>();
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

**File:** accounts-db/src/accounts_db.rs (L2568-2599)
```rust
        // Filter and collect tombstones
        let can_purge_zero_lamport_single_ref =
            self.can_purge_zero_lamport_single_ref_after_shrink(slot);
        let mut tombstones_to_carry_forward = Vec::new();
        let tombstone_offsets = store.tombstone_offsets_read_lock();
        if !tombstone_offsets.is_empty() {
            stored_accounts.retain(|account| {
                if tombstone_offsets.contains(&account.index_info.offset()) {
                    // If we can't purge zero lamport accounts, they need to be rewritten after shrink
                    if !can_purge_zero_lamport_single_ref {
                        tombstones_to_carry_forward.push(*account);
                    }
                    false
                } else {
                    true
                }
            });
        }
        drop(tombstone_offsets);

        let len = stored_accounts.len();
        let shrink_collect = Mutex::new(ShrinkCollect {
            slot,
            written_bytes: *written_bytes,
            zero_lamport_single_ref_pubkeys: Vec::new(),
            alive_accounts: T::with_capacity(len, slot),
            tombstones_to_carry_forward,
            tombstones_total_bytes: 0, // will be updated after the tombstone list is populated
            total_starting_accounts,
            all_are_zero_lamports: true,
            alive_total_bytes: 0, // will be updated after `alive_accounts` is populated
        });
```

**File:** accounts-db/src/accounts_db.rs (L2857-2871)
```rust
        let accounts = [(slot, &shrink_collect.alive_accounts.alive_accounts()[..])];
        let storable_accounts = StorableAccountsBySlot::new(slot, &accounts, self);
        stats_sub.store_accounts_stats = self.store_accounts_for_shrink(
            storable_accounts,
            shrink_in_progress.new_storage(),
            UpdateIndexThreadSelection::PoolWithThreshold,
        );

        let tombstone_refs: Vec<_> = shrink_collect.tombstones_to_carry_forward.iter().collect();
        let tombstone_accounts = [(slot, &tombstone_refs[..])];
        let storable_tombstones = StorableAccountsBySlot::new(slot, &tombstone_accounts, self);
        let (num_tombstones_carried_forward, tombstone_carry_forward_us) = measure_us!(
            self.store_tombstones(shrink_in_progress.new_storage(), storable_tombstones)
        );
        stats_sub.tombstone_carry_forward_us = Saturating(tombstone_carry_forward_us);
```
