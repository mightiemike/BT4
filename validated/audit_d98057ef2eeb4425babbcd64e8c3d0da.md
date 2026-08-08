### Title
`SnapshotMinimizer::filter_storage` ignores `purge_keys_exact` reclaim results, leaving stale account-index/storage accounting during minimized-snapshot generation - (File: runtime/src/snapshot_minimizer.rs)

### Summary
`SnapshotMinimizer::filter_storage` calls `self.accounts_db().purge_keys_exact(purge_pubkeys)` and discards the returned reclaims with `let _ = ...`, instead of feeding them into `handle_reclaims` the way `AccountsDb::clean_accounts` does. This mirrors the reported bug class ("ignores return value") and is reachable in the accounts-hashing/snapshot-generation surface named in scope.

### Finding Description
`purge_keys_exact` removes pubkeys from the accounts index for a given set of (pubkey, slot) pairs and returns the set of reclaimed `(slot, account_info)` entries so the caller can update storage-side bookkeeping (alive-byte counts, marking storages dead, etc.). This is exactly how `AccountsDb::clean_accounts` uses it: [1](#0-0) 
There, the reclaims are passed to `self.handle_reclaims(...)`, which decrements the alive-account/alive-bytes counters of the storages that held the now-removed accounts, and returns the set of storages that became fully dead so they can be dropped.

In `SnapshotMinimizer::filter_storage`, the same `purge_keys_exact` call is made when building a minimized snapshot, but the result is thrown away: [2](#0-1) 

Because the reclaims are never routed through `handle_reclaims` (or an equivalent), the storages that contained the purged pubkeys (which may live in *other* slots than the one currently being shrunk in `filter_storage`) never have their alive-byte/alive-account counters decremented, and are never marked dead/queued for cleanup even though their account was just removed from the index. The `purge_pubkeys` fed into `purge_keys_exact` come from `self.accounts_db().contains(account.pubkey())` accounts collected while iterating **all** storages during minimization, so the affected storages are not necessarily the current `storage`/`slot` being shrunk in this call — they can be storages at other slots.

### Impact Explanation
- Storages holding a purged pubkey elsewhere will retain stale alive-byte counts, understating how “dead” they are and preventing `is_shrinking_productive`/`is_candidate_for_shrink` from correctly deciding they are worth shrinking or dropping.
- Because the index entry is gone but the storage-side bookkeeping is not updated, this reproduces the class of "storage accounting divergence" that can affect `capitalization`/accounts lt hash consistency for the minimized snapshot workflow and cause an honest replayer to see storage/count mismatches (dead accounts not swept, disproportionate storage retained) versus what generate-index/clean would otherwise produce.
- This is CPU/storage-cost disproportion and snapshot-vs-expected-state divergence, which the scan criteria explicitly calls out as an acceptable outcome class ("disproportionate storage and CPU cost", "honest-node snapshot-vs-replay mismatch").

### Likelihood Explanation
`SnapshotMinimizer` is `#[cfg(feature = "dev-context-only-utils")]`-gated, per the module header: [3](#0-2) 
This means it's a dev/test/tooling utility (used by `ledger-tool` for creating minimized snapshots), not part of the always-on unprivileged-user validator hot path. It runs whenever an operator uses `ledger-tool` to create a minimized snapshot, an operation an unprivileged network participant does not trigger remotely — the trigger is local tooling invocation, not attacker-controlled input over the network.

### Recommendation
Route the value returned by `purge_keys_exact` in `filter_storage` through `handle_reclaims` (matching the pattern used in `clean_accounts`) so that storages affected by the purge have their alive-byte/alive-account bookkeeping updated and dead storages are correctly identified, instead of discarding the reclaims with `let _ = ...`.

### Proof of Concept
Not applicable as a remote/attacker-triggerable PoC: `SnapshotMinimizer` is a `dev-context-only-utils`-gated tool invoked locally (e.g., via `ledger-tool`) by an operator, not reachable by an unprivileged network peer. A concrete reproduction would require running the minimized-snapshot creation path locally and observing storage alive-byte/alive-account counters for a slot other than the one being filtered remain stale after `purge_keys_exact` removes an account that also lived in that other slot — this could not be fully verified via static code search alone within the available context and would need dynamic testing to confirm exact runtime consequences (e.g., whether shrink/clean later self-corrects the stale counters on the next full pass).

### Citations

**File:** accounts-db/src/accounts_db.rs (L2120-2131)
```rust
        let reclaims = self.purge_keys_exact(pubkey_to_slot_set);

        if !reclaims.is_empty() {
            let expected_dead_slots: IntSet<_> = reclaims.iter().map(|(slot, _)| *slot).collect();
            let dead_slots = self.handle_reclaims(
                reclaims.iter(),
                &self.clean_accounts_stats.purge_stats,
                MarkAccountsObsolete::No,
            );
            // Every slot with accounts reclaimed should be marked dead
            assert_eq!(expected_dead_slots, dead_slots);
        }
```

**File:** runtime/src/snapshot_minimizer.rs (L1-3)
```rust
//! Used to create minimal snapshots - separated here to keep accounts_db simpler
#![cfg(feature = "dev-context-only-utils")]

```

**File:** runtime/src/snapshot_minimizer.rs (L300-327)
```rust
        let keep_accounts = keep_accounts_collect.into_inner().unwrap();
        let remove_pubkeys = purge_pubkeys_collect.into_inner().unwrap();
        let total_bytes = total_bytes_collect.load(Ordering::Relaxed);

        let purge_pubkeys = remove_pubkeys.into_iter().map(|pubkey| (*pubkey, slot));
        let _ = self.accounts_db().purge_keys_exact(purge_pubkeys);

        let mut shrink_in_progress = None;
        if total_bytes > 0 {
            shrink_in_progress = Some(self.accounts_db().get_store_for_shrink(
                slot,
                Arc::clone(storage),
                total_bytes as u64,
            ));
            let new_storage = shrink_in_progress.as_ref().unwrap().new_storage();

            let accounts = [(slot, &keep_accounts[..])];
            let storable_accounts =
                StorableAccountsBySlot::new(slot, &accounts, self.accounts_db());

            self.accounts_db().store_accounts_for_shrink(
                storable_accounts,
                new_storage,
                UpdateIndexThreadSelection::Inline,
            );

            new_storage.flush().unwrap();
        }
```
