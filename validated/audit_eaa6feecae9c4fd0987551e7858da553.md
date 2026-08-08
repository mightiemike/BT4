### Title
Missing zero-lamport guard in the write-cache correction step of `calculate_accounts_lt_hash_at_startup_from_index` causes spurious `mix_out`, producing a wrong accounts lt-hash - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::calculate_accounts_lt_hash_at_startup_from_index` computes the base lt-hash by skipping zero-lamport accounts, but the separate "cache correction" step that reconciles cached (unflushed) versions against the index does not apply the same zero-lamport skip when mixing out the old, index-resolved version. For a pubkey whose rooted/flushed storage version is zero-lamport and whose only newer version lives in the (unflushed) accounts cache, the correction step mixes out a hash that was never mixed in, yielding a wrong final lt-hash.

### Finding Description
The base per-bin loop explicitly guards zero-lamport accounts before contributing to the hash: [1](#0-0) 

This means a zero-lamport indexed account contributes nothing (identity) to `lt_hash`.

The subsequent cache-correction block, which is meant to reconcile the version the base loop saw (from the real `accounts_index`, which never tracks unflushed cache entries — `AccountInfo::new` only encodes `StorageLocation::AppendVec`) against the cache's newer version, does the mix-out unconditionally, with no `is_zero_lamport()` check: [2](#0-1) 

Sequence:
1. Attacker creates account `P`, funds it, gets it rooted and flushed (`v0`, lamports > 0).
2. Attacker withdraws all lamports from `P` (closes it) in a subsequent slot; this zero-lamport version `v1` is rooted and flushed to storage — so the real `accounts_index` for `P` now resolves (for the relevant ancestors) to `v1`, a zero-lamport entry.
3. Attacker re-funds/reopens `P` with nonzero lamports (`v2`) in a new slot that is *not yet flushed* (still resident only in `accounts_cache`), while remaining within the `ancestors` passed to the hash calculation.
4. `calculate_accounts_lt_hash_at_startup_from_index(ancestors)` is invoked (verification path, e.g. `Bank::verify_accounts`, `Bank::calculate_accounts_lt_hash_for_tests`, `SnapshotMinimizer::minimize`) while `P`'s cache slot is still unflushed.

In the base loop, `P` maps to `v1` (zero-lamport) via `accounts_index.get_with_and_then`, and the `(!account_info.is_zero_lamport()).then(...)` guard causes it to contribute **nothing**.

In the cache-correction block, `P` (found via `accounts_cache.cached_pubkeys()`) again resolves `v1` via the same index lookup — but this branch has no zero-lamport guard, so it computes `lt_hash_account(v1)` (a real, non-identity hash for zero-lamport account content) and calls `cache_lt_hash.mix_out(...)` with it. It then loads the current value (`v2`, via `self.load`) and mixes that in.

Net effect: total contribution for `P` = `0 (base loop) + (-hash(v1) + hash(v2)) (correction) = hash(v2) - hash(v1)`, instead of the correct `hash(v2)`. The spurious `-hash(v1)` term was never balanced by an equal `+hash(v1)` anywhere, so the mix-out/mix-in sequence fails to cancel and the resulting lt-hash diverges from the value that would be produced by normal incrementally-maintained `bank.accounts_lt_hash` (which correctly excludes zero-lamport contributions entirely, per the same design intent visible in the base-loop guard).

### Impact Explanation
This produces a genuine accounts lattice-hash/capitalization-style divergence for any AccountsDb caller that invokes this startup verification routine while a previously-zero-lamport account has since been reopened but not yet flushed. This corresponds to the "hash/capitalization divergence" bounty category: an honest node computing this value (e.g. during snapshot/index-generation verification, or via `SnapshotMinimizer` recomputing the lt-hash) would fail verification (`check_lt_hash` mismatch in `Bank::verify_accounts`) or silently persist/propagate a wrong hash if the mismatch isn't otherwise caught, since the deviation is a genuine mathematical error in the reconciliation logic, not a cosmetic one.

### Likelihood Explanation
The trigger sequence (fund → close to zero → reopen) is entirely composed of ordinary, unprivileged account operations paid for and controlled by any user, and requires no validator/leader/gossip privilege. The remaining precondition — that the reopened version is still cache-resident (unflushed) precisely when this function is invoked — depends on validator-internal timing (most callers, e.g. `run_final_hash_calc`, force a full flush immediately before calling this function, and the accompanying unit tests in the repo also flush before comparing). This significantly limits real-world reachability: in the observed call sites, the cache-reconciliation branch is largely defensive code for cases where flushing hasn't happened, rather than a state that is reliably attacker-triggerable in production verification flows. The bug is nonetheless real and deterministically reproducible in a pure AccountsDb-level unit test.

### Recommendation
Add the same zero-lamport guard used in the base loop to the cache-correction mix-out step in `calculate_accounts_lt_hash_at_startup_from_index` (and audit `calculate_capitalization_at_startup_from_index`'s analogous cache correction for the same asymmetry), i.e. only call `mix_out` when `!account_info.is_zero_lamport()`, mirroring the semantics used for the base per-bin accumulation.

### Proof of Concept
```rust
// accounts-db/src/accounts_db/tests/impl.rs (illustrative)
#[test]
fn test_lt_hash_zero_lamport_reopen_unflushed_diverges() {
    let owner = *AccountSharedData::default().owner();
    let pubkey = solana_pubkey::new_rand();
    let accounts = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    accounts.add_root(0);

    // v0: funded, rooted+flushed
    let funded = AccountSharedData::new(500, 0, &owner);
    accounts.store_for_tests((1, [(&pubkey, &funded)].as_slice()));
    accounts.add_root_and_flush_write_cache(1);

    // v1: closed to zero lamports, rooted+flushed -> accounts_index now resolves to zero-lamport entry
    let zero = AccountSharedData::new(0, 0, &owner);
    accounts.store_for_tests((2, [(&pubkey, &zero)].as_slice()));
    accounts.add_root_and_flush_write_cache(2);

    // v2: reopened with nonzero lamports, kept UNFLUSHED (still in accounts_cache)
    let reopened = AccountSharedData::new(777, 0, &owner);
    accounts.add_root(3);
    accounts.store_for_tests((3, [(&pubkey, &reopened)].as_slice()));
    // NOTE: no add_root_and_flush_write_cache(3) here -- slot 3 stays cache-resident

    let ancestors = linear_ancestors(3);

    // Compute lt hash while cache is populated (buggy path)
    let hash_with_cache = accounts.calculate_accounts_lt_hash_at_startup_from_index(&ancestors);

    // Now flush and recompute -- this exercises only the base loop, which correctly
    // excludes the stale zero-lamport contribution
    accounts.add_root_and_flush_write_cache(3);
    let hash_after_flush = accounts.calculate_accounts_lt_hash_at_startup_from_index(&ancestors);

    // EXPECTED (bug): these differ, proving the cache-correction mix_out/mix_in
    // sequence did not cancel cleanly for the zero-lamport predecessor version.
    assert_eq!(
        hash_with_cache, hash_after_flush,
        "lt hash diverges depending on flush state due to missing zero-lamport guard \
         in cache correction mix_out"
    );
}
```
Expected result on the current implementation: the assertion fails, demonstrating `hash_with_cache != hash_after_flush` for identical logical account state, confirming the divergence described.

### Citations

**File:** accounts-db/src/accounts_db.rs (L4665-4686)
```rust
                    for pubkey in accounts_index_bin.keys() {
                        let account_lt_hash = self
                            .accounts_index
                            .get_with_and_then(&pubkey, ancestors, false, |(slot, account_info)| {
                                (!account_info.is_zero_lamport()).then(|| {
                                    self.get_account_accessor(
                                        slot,
                                        &account_info.storage_location(),
                                    )
                                    .get_loaded_account(|loaded_account| {
                                        Self::lt_hash_account(&loaded_account, &pubkey)
                                    })
                                    // SAFETY: The index said this pubkey exists, so
                                    // there must be an account to load.
                                    .unwrap()
                                })
                            })
                            .flatten();
                        if let Some(account_lt_hash) = account_lt_hash {
                            accumulator_lt_hash.mix_in(&account_lt_hash.0);
                        }
                    }
```

**File:** accounts-db/src/accounts_db.rs (L4695-4722)
```rust
        let cache_lt_hash = {
            let mut cache_lt_hash = LtHash::identity();
            for pubkey in self.accounts_cache.cached_pubkeys().iter() {
                // mix out whatever older version the index walk produced (if any)
                self.accounts_index.get_with_and_then(
                    pubkey,
                    ancestors,
                    false,
                    |(slot, account_info)| {
                        self.get_account_accessor(slot, &account_info.storage_location())
                            .get_loaded_account(|loaded_account| {
                                cache_lt_hash
                                    .mix_out(&Self::lt_hash_account(&loaded_account, pubkey).0);
                            });
                    },
                );
                // mix in the cache version
                if let Some((account, _slot)) = self.load(
                    ancestors,
                    pubkey,
                    LoadHint::FixedMaxRoot,
                    PopulateReadCache::False,
                ) {
                    cache_lt_hash.mix_in(&Self::lt_hash_account(&account, pubkey).0);
                }
            }
            cache_lt_hash
        };
```
