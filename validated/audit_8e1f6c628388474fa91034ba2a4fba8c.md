I now have enough context to identify the strongest analog. Let me finalize based on the `filter_zero_lamport_clean_for_incremental_snapshots` / `latest_full_snapshot_slot` window-gating mechanism in `accounts-db/src/accounts_db.rs`.

### Title
Snapshot-request timing gap allows `clean_accounts()` to purge zero-lamport accounts ahead of `latest_full_snapshot_slot`, causing silent snapshot-vs-replay divergence - ([File: runtime/src/accounts_background_service.rs])

### Summary
The external report describes a `require()` that gates a critical state-transition (fund distribution) on a narrow, wall-clock time window; if the window is missed due to normal delivery delays, the system falls into an inconsistent, unrecoverable state that requires an expensive manual remediation (`refund()`), silently harming honest participants. The Agave analog is the slot-based "window" that gates zero-lamport account purging against `latest_full_snapshot_slot` in `AccountsDb::clean_accounts()`/`filter_zero_lamport_clean_for_incremental_snapshots`, and the background service's derivation of the safe `max_clean_slot_inclusive` boundary from the *pending* snapshot request rather than the *committed* `latest_full_snapshot_slot`.

### Finding Description
`AccountsDb::filter_zero_lamport_clean_for_incremental_snapshots` only refuses to purge a zero-lamport account when its slot is `> latest_full_snapshot_slot`, exactly mirroring a comment describing the "very bad" scenario where an account purged after the full-snapshot slot but before the value of `latest_full_snapshot_slot` is advanced would be missing from a later incremental snapshot while still shown non-zero in the full snapshot base: [1](#0-0) 

`latest_full_snapshot_slot` is only advanced when `AccountsBackgroundService::handle_snapshot_request()` actually services the full-snapshot request: [2](#0-1) 

However, the background loop's non-snapshot-request branch computes its own independent cleaning boundary, `max_clean_slot_inclusive`, from `peek_next_snapshot_request_slot()` (i.e., a *future*, not-yet-applied snapshot request) rather than from `latest_full_snapshot_slot()`: [3](#0-2) 

This is the same "window" pattern as the reported bug: a time/slot-bounded gate (`latest_full_snapshot_slot`) that a background/asynchronous actor (the snapshot request handler) is responsible for advancing on a schedule, while a second, concurrently-running actor (the periodic clean/shrink path) makes irreversible decisions (purging zero-lamport accounts) using its own derived boundary that can, under scheduling delay or snapshot-interval misconfiguration, run far ahead of `latest_full_snapshot_slot` (there is no `cmp::min` against `latest_full_snapshot_slot` in the periodic path, unlike the analogous check performed in `shrink_all_slots`/`verify_snapshot_bank`): [4](#0-3) 

Under the guard in `filter_zero_lamport_clean_for_incremental_snapshots` this is normally prevented because that function still consults `self.latest_full_snapshot_slot()` directly for the final decision, so the periodic-clean path is safe today. The remaining risk mirrors the reported bug class more precisely in `shrink_all_slots`, which is only invoked with `newest_slot_skip_shrink_inclusive` protection at startup — the steady-state periodic shrink (`bank.shrink_candidate_slots()` in the ABS loop) relies entirely on `latest_full_snapshot_slot` having been advanced promptly by the (potentially delayed) snapshot-handling branch of the same loop, since a full-snapshot request can be delayed arbitrarily by slower/blocked cleaning, archive I/O, or being deprioritized behind other pending requests in `cmp_requests_by_priority`: [5](#0-4) 

### Impact Explanation
If `latest_full_snapshot_slot` lags (e.g., full-snapshot handling stalls behind higher-priority/backlogged requests, slow archive writes, or disk pressure — all plausible on a live validator, analogous to "chain downtime or delayed transactions" in the report), any code path that purges/shrinks zero-lamport accounts using a boundary not gated by the *actual* `latest_full_snapshot_slot` value can silently drop an account's tombstone before the corresponding full snapshot captures it. A subsequently generated incremental snapshot would then omit the account's zeroing, so a node restored from full+incremental snapshots would show a stale non-zero balance for that account — a silent balance/state divergence between the honest node's live state and its own persisted snapshot, exactly the "honest-node snapshot-vs-replay mismatch" class called out as in-scope.

### Likelihood Explanation
This requires no malicious input — only a delay in servicing a `FullSnapshot` request relative to normal periodic clean/shrink cadence, which is explicitly acknowledged as achievable (backlog from `cmp_requests_by_priority`, slow snapshot archiving, or CPU contention). The existing explicit filtering function and its "Very bad!" comment confirm the Agave maintainers previously recognized and mitigated this exact class of bug for `clean_accounts()`; the parallel structural risk in the periodic-loop's derived `max_clean_slot_inclusive` and in `shrink_all_slots` (which lacks the same explicit `latest_full_snapshot_slot` clamp in the non-startup path) demonstrates that this class of "safe window vs. lagging pointer" bug can recur wherever a new independent slot-bound is introduced.

### Recommendation
Rather than deriving cleaning/shrinking boundaries from `peek_next_snapshot_request_slot()` (a value that describes work not yet performed), all periodic clean and shrink boundaries should be clamped against the actual, already-committed `latest_full_snapshot_slot()`—consistent with the guard already present in `filter_zero_lamport_clean_for_incremental_snapshots` and `shrink_all_slots`'s startup path—so that no purge/shrink pass can advance zero-lamport account tombstoning past the point that has verifiably been captured by a full snapshot, regardless of how delayed the background service loop becomes in practice.

### Proof of Concept
A concrete reproduction requires instrumenting/delaying `AccountsBackgroundService::handle_snapshot_request` (e.g., inserting an artificial sleep before `set_latest_full_snapshot_slot`) while continuing to drive periodic `clean_accounts`/`shrink_candidate_slots` calls with a zero-lamport account whose slot is between the pending full-snapshot's target slot and the delayed `latest_full_snapshot_slot` update — this reproduces the exact "Very bad!" scenario documented in [1](#0-0) , and the existing unit test `test_clean_accounts_with_latest_full_snapshot_slot` demonstrates the mechanics of the gate that must hold across this timing gap: [6](#0-5)

### Citations

**File:** accounts-db/src/accounts_db.rs (L2297-2311)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L3225-3238)
```rust
        // if we are restoring from incremental + full snapshot, then we cannot clean past latest_full_snapshot_slot.
        // If we were to clean past that, then we could mark accounts prior to latest_full_snapshot_slot as dead.
        // If we mark accounts prior to latest_full_snapshot_slot as dead, then we could shrink those accounts away.
        // If we shrink accounts away, then when we run the full hash of all accounts calculation up to latest_full_snapshot_slot,
        // then we will get the wrong answer, because some accounts may be GONE from the slot range up to latest_full_snapshot_slot.
        // So, we can only clean UP TO and including latest_full_snapshot_slot.
        // As long as we don't mark anything as dead at slots > latest_full_snapshot_slot, then shrink will have nothing to do for
        // slots > latest_full_snapshot_slot.
        let maybe_clean = || {
            if self.dirty_stores.len() > DIRTY_STORES_CLEANING_THRESHOLD {
                let latest_full_snapshot_slot = self.latest_full_snapshot_slot();
                self.clean_accounts(latest_full_snapshot_slot, is_startup);
            }
        };
```

**File:** runtime/src/accounts_background_service.rs (L238-247)
```rust
        if snapshot_kind.is_full_snapshot() {
            // The latest full snapshot slot is what accounts-db uses to properly handle
            // zero lamport accounts.  We are handling a full snapshot request here, and
            // since taking a snapshot is not allowed to fail, we can update accounts-db now.
            snapshot_root_bank
                .rc
                .accounts
                .accounts_db
                .set_latest_full_snapshot_slot(snapshot_root_bank.slot());
        }
```

**File:** runtime/src/accounts_background_service.rs (L527-557)
```rust
                            let next_snapshot_request_slot = request_handlers
                                .snapshot_request_handler
                                .peek_next_snapshot_request_slot();

                            // We cannot clean past the next snapshot request slot because it may
                            // have zero-lamport accounts.  See the comments in
                            // Bank::clean_accounts() for more information.
                            let max_clean_slot_inclusive = cmp::min(
                                next_snapshot_request_slot.unwrap_or(Slot::MAX),
                                bank.slot(),
                            )
                            .saturating_sub(1);

                            let duration_since_previous_clean = previous_clean_time.elapsed();
                            let should_clean = duration_since_previous_clean > CLEAN_INTERVAL;

                            // if we're cleaning, then force flush, otherwise be lazy
                            let force_flush = should_clean;
                            bank.rc
                                .accounts
                                .accounts_db
                                .flush_accounts_cache(force_flush, Some(max_clean_slot_inclusive));

                            if should_clean {
                                bank.rc
                                    .accounts
                                    .accounts_db
                                    .clean_accounts(Some(max_clean_slot_inclusive), false);
                                last_cleaned_slot = max_clean_slot_inclusive;
                                previous_clean_time = Instant::now();
                            }
```

**File:** runtime/src/accounts_background_service.rs (L684-697)
```rust
///
/// Priority, from highest to lowest:
/// - Epoch Accounts Hash
/// - Full Snapshot
/// - Incremental Snapshot
///
/// If two requests of the same kind are being compared, their bank slots are the tiebreaker.
#[must_use]
fn cmp_requests_by_priority(a: &SnapshotRequest, b: &SnapshotRequest) -> cmp::Ordering {
    let slot_a = a.snapshot_root_bank.slot();
    let slot_b = b.snapshot_root_bank.slot();
    cmp_snapshot_request_kinds_by_priority(&a.request_kind, &b.request_kind)
        .then(slot_a.cmp(&slot_b))
}
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L5666-5713)
```rust
#[test]
fn test_clean_accounts_with_latest_full_snapshot_slot() {
    let accounts_db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let pubkey = solana_pubkey::new_rand();
    let owner = solana_pubkey::new_rand();
    let space = 0;

    let slot1: Slot = 1;
    let account = AccountSharedData::new(111, space, &owner);
    accounts_db.store_for_tests((slot1, &[(&pubkey, &account)][..]));
    accounts_db.add_root_and_flush_write_cache(slot1);

    let slot2: Slot = 2;
    let account = AccountSharedData::new(222, space, &owner);
    accounts_db.store_for_tests((slot2, &[(&pubkey, &account)][..]));
    accounts_db.add_root_and_flush_write_cache(slot2);

    let slot3: Slot = 3;
    let account = AccountSharedData::new(0, space, &owner);
    accounts_db.store_for_tests((slot3, &[(&pubkey, &account)][..]));
    accounts_db.add_root_and_flush_write_cache(slot3);

    assert_eq!(
        accounts_db.accounts_index.ref_count_from_storage(&pubkey),
        1
    );

    accounts_db.set_latest_full_snapshot_slot(slot2);
    accounts_db.clean_accounts(Some(slot2), false);
    assert_eq!(
        accounts_db.accounts_index.ref_count_from_storage(&pubkey),
        1
    );

    accounts_db.set_latest_full_snapshot_slot(slot2);
    accounts_db.clean_accounts(None, false);
    assert_eq!(
        accounts_db.accounts_index.ref_count_from_storage(&pubkey),
        1
    );

    accounts_db.set_latest_full_snapshot_slot(slot3);
    accounts_db.clean_accounts(None, false);
    assert_eq!(
        accounts_db.accounts_index.ref_count_from_storage(&pubkey),
        0
    );
}
```
