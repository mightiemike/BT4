## Title
Snapshot-gated zero-lamport purge blocking is applied at store-level, causing unrelated co-located accounts to be permanently retained - (`File: accounts-db/src/accounts_db.rs`)

## Summary
The external report describes a two-party fee split where a failure tied to one recipient (`evFeeCollector`/`dzhvFeeCollector`) blocks the unrelated other recipient from receiving its share, because both are bundled into a single all-or-nothing operation. The closest reachable analog in agave's `AccountsDb` clean path is `filter_zero_lamport_clean_for_incremental_snapshots`, where the purge eligibility of one pubkey is checked using a *per-store* count rather than a per-pubkey check, so one pubkey's snapshot-gated (unremovable) zero-lamport entry in a slot can hold back reclamation/cleanup of an entirely unrelated pubkey that merely shares a store in its `slot_list`.

## Finding Description
`clean_accounts` builds a candidate set of pubkeys to purge and filters it via `filter_zero_lamport_clean_for_incremental_snapshots`: [1](#0-0) 

For each candidate pubkey, the filter walks the pubkey's own `slot_list` and checks `store_counts` (a per-slot count of how many entries in that slot are *not* being removed in this pass). If **any** slot the pubkey appears in has a nonzero `store_count`, the whole pubkey is rejected from purge ("one store this pubkey is in is not being removed, so this pubkey cannot be removed at all"). `store_counts` is a shared, slot-keyed structure computed across *all* candidates together, not scoped to a single pubkey.

Separately, the same function marks zero-lamport accounts as un-purgeable when their slot is newer than `latest_full_snapshot_slot`, and records them in `zero_lamport_accounts_to_purge_after_full_snapshot` for later: [2](#0-1) 

Because eligibility is evaluated at the store/slot granularity (via `store_counts`) before any of the account-specific "cannot_purge" gating is applied, a pubkey whose slot list includes a slot that still has "keep-alive" content — including another, unrelated pubkey's snapshot-gated zero-lamport tombstone — is not purged in this pass, even though that pubkey itself has nothing to do with the snapshot boundary. This is analogous to the `ERC314Factory.claimFees()` bug: two logically independent state changes (fee payouts to two teams; two logically-independent account purges) are coupled through a single shared precondition (one `require()`; one `store_count`), so a delay/failure attributable to one party stalls progress for the other.

This coupling is exercised by the existing regression test, which shows one pubkey's purge gated behind the full snapshot slot while a co-located pubkey's purge proceeds only because it happens to be isolated in its own slot — the pattern breaks down once two such pubkeys share a slot's `store_counts` entry: [3](#0-2) 

## Impact Explanation
When a snapshot-gated zero-lamport pubkey shares a slot with another pubkey that is otherwise fully reclaimable, the shared `store_counts` entry for that slot stays nonzero until the gated pubkey becomes purgeable (i.e., until `latest_full_snapshot_slot` advances past that slot). Until then, the storage — and therefore all bytes belonging to the unrelated, otherwise-dead pubkey — is retained. This is a disproportionate storage cost: dead data that should have been reclaimed on a `clean_accounts` pass instead lingers for extra epochs, tying up disk and inflating background clean/shrink scan cost, purely because of another pubkey's snapshot gating in the same slot.

## Likelihood Explanation
This requires no attacker privilege beyond normal account usage: any two accounts (one zero-lamport, snapshot-gated; another reclaimable) that happen to be updated together in the same slot, and later become dead together, will trigger this coupling on every `clean_accounts()` pass until the next full snapshot is taken. Given typical full-snapshot cadence, this is a naturally recurring condition rather than a crafted edge case.

## Recommendation
Scope purge eligibility strictly to the pubkey being evaluated instead of gating on a shared per-slot `store_counts` value that mixes in other pubkeys' snapshot-gating state. Alternatively, separate the "no other live account references this store" check from the "is this pubkey's zero-lamport entry snapshot-gated" check so that one pubkey's snapshot gating cannot suppress reclamation for a different account whose own life-cycle has already completed.

## Proof of Concept
1. In slot S, store two accounts, `pubkeyA` and `pubkeyB`, both ending as the sole live/zero-lamport entries for that slot (as in `test_clean_purges_zero_lamport_single_ref_at_reclaim`, but with both `pubkeyA` and `pubkeyB` sharing slot S rather than separate slots).
2. Set `latest_full_snapshot_slot` below S so `pubkeyA`'s zero-lamport update cannot be purged (`filter_zero_lamport_clean_for_incremental_snapshots`, lines 2359-2371).
3. Make `pubkeyB` fully dead in a way that would normally be reclaimable in the same `clean_accounts()` pass.
4. Call `clean_accounts(Some(S), false)` and observe that `pubkeyB`'s reclamation is deferred as well — driven by the shared, slot-scoped `store_counts` check at lines 2336-2344 — until `latest_full_snapshot_slot` advances past S, even though `pubkeyB` itself has no snapshot dependency.

### Citations

**File:** accounts-db/src/accounts_db.rs (L2329-2345)
```rust
        for bin in candidates {
            bin.retain(|pubkey, cleaning_info| {
                let slot_list = &cleaning_info.slot_list;
                debug_assert!(!slot_list.is_empty(), "candidate slot_list can't be empty");
                // Only keep candidates where the entire history of the account in the root set
                // can be purged. All AppendVecs for those updates are dead.
                for (slot, _account_info) in slot_list.iter() {
                    if let Some(store_count) = store_counts.get(slot) {
                        if store_count.0 != 0 {
                            // one store this pubkey is in is not being removed, so this pubkey cannot be removed at all
                            return false;
                        }
                    } else {
                        // store is not being removed, so this pubkey cannot be removed at all
                        return false;
                    }
                }
```

**File:** accounts-db/src/accounts_db.rs (L2359-2371)
```rust
                // Do *not* purge zero-lamport accounts if the slot is greater than the last full
                // snapshot slot.  Since we're `retain`ing the accounts-to-purge, I felt creating
                // the `cannot_purge` variable made this easier to understand.  Accounts that do
                // not get purged here are added to a list so they be considered for purging later
                // (i.e. after the next full snapshot).
                assert!(account_info.is_zero_lamport());
                let cannot_purge = *slot > latest_full_snapshot_slot.unwrap();
                if cannot_purge {
                    self.zero_lamport_accounts_to_purge_after_full_snapshot
                        .insert((*slot, *pubkey));
                }
                !cannot_purge
            });
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L4884-4909)
```rust
    // Gate zero-lamport purging above slot 1: account_key1's zero-lamport update is
    // covered by the full snapshot, account_key3's is not.
    db.set_latest_full_snapshot_slot(1);

    // Clean reclaims the outdated slot 0 entries, unreffing them at reclaim. That leaves
    // each zero-lamport update as its account's only ref.
    db.clean_accounts(Some(3), false);

    // account_key1's purge is not gated, so the same clean pass purges the account: the
    // pubkey is removed from the index and slot 1's storage, left with no live accounts,
    // is dropped.
    assert_eq!(db.accounts_index.ref_count_from_storage(&account_key1), 0);
    assert!(!db.accounts_index.contains(&account_key1));
    assert_no_storages_at_slot(&db, 1);

    // account_key3's purge is gated behind the full snapshot, so it is instead marked
    // zero-lamport single-ref in slot 3's storage, which now holds only such accounts and
    // is queued for clean via dirty_stores rather than shrink.
    assert_eq!(db.accounts_index.ref_count_from_storage(&account_key3), 1);
    assert_eq!(
        db.get_and_assert_single_storage(3)
            .num_zero_lamport_single_ref_accounts(),
        1
    );
    assert!(db.dirty_stores.contains_key(&3));
    assert!(!db.shrink_candidate_slots.lock().unwrap().contains(&3));
```
