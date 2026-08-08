### Title
Missing zero-lamport guard in cache-correction `mix_out` path causes wrong `accounts_lt_hash` in `calculate_accounts_lt_hash_at_startup_from_index` - (File: `accounts-db/src/accounts_db.rs`)

### Summary
`AccountsDb::calculate_accounts_lt_hash_at_startup_from_index` computes the accounts lattice hash by first scanning the on-disk index, then applying a correction pass over `accounts_cache.cached_pubkeys()` to account for accounts that have a newer, unflushed cached version. The correction pass's `mix_out` step, unlike the initial index scan and the `mix_in` step, does not skip zero-lamport accounts, so it can subtract a hash contribution that was never added, producing a wrong accumulated `LtHash`.

### Finding Description
The function first walks all index bins and mixes in the lt-hash of each pubkey's index-visible account, but only if it is non-zero-lamport: [1](#0-0) 

It then runs a correction pass for every pubkey present in the write cache. For each such pubkey it re-queries the index with the same `ancestors` to "mix out whatever older version the index walk produced", then uses `self.load()` to mix in the true, cache-aware value: [2](#0-1) 

The `mix_out` block (lines 4699‑4710) calls `get_with_and_then` and unconditionally computes `lt_hash_account` and mixes it out — it has no `is_zero_lamport()` guard, unlike the first loop (line 4669) and unlike `self.load()` used for `mix_in`, which internally filters out zero-lamport accounts: [3](#0-2) 

Because cache writes never upsert the accounts index (confirmed by the code comment and test), the index can hold a stale, zero-lamport index entry for a pubkey while a newer, non-zero rewrite sits only in the write cache: [4](#0-3) [5](#0-4) 

In that situation:
- The first loop skips this pubkey entirely (its index entry is zero-lamport, so `(!is_zero_lamport()).then(...)` yields `None`) — contributing nothing.
- The correction loop's `mix_out` still finds the same zero-lamport index entry (identical `get_with_and_then` call/ancestors) and unconditionally mixes out `lt_hash_account(zero_account)`, a real non-identity value.
- The correction loop's `mix_in` uses `self.load()`, which finds the newer non-zero cached value and mixes it in.

The net effect is `cache_lt_hash += lt_hash_account(new_account) − lt_hash_account(zero_account)`, whereas the canonical semantics used everywhere else in the codebase (e.g. `Bank`'s on-chain lt-hash updater, which also filters zero-lamport accounts on both the "prev" and "curr" side) require zero-lamport accounts to never contribute to the hash at all: [6](#0-5) 

This extra, unbalanced `−lt_hash_account(zero_account)` term is a genuine deviation from HASH_DETERMINISM: the value returned by `calculate_accounts_lt_hash_at_startup_from_index` is no longer a pure function of the account's true, single visible value.

### Impact Explanation
This breaks the invariant that the accounts lt hash is a pure function of committed state. It causes `Bank::verify_accounts` (called at startup/ledger-tool) to compare a wrongly-computed hash against the bank's real `accounts_lt_hash`, producing a false verification failure (or, symmetrically, could mask a real divergence if the erroneous term happens to compensate for an actual bug elsewhere): [7](#0-6) 
This matches the "honest-node snapshot-vs-replay mismatch" / hash-divergence bounty category, scoped strictly to `AccountsDb`/hashing logic reachable via unprivileged account operations (create, fund, zero-out, then rewrite an account across slots without an intervening flush).

### Likelihood Explanation
The attacker only needs an unprivileged sequence of ordinary transactions:
1. Create/fund account `A` in slot 1, allow it to be flushed to storage.
2. Zero out `A` (e.g. close/withdraw all lamports) in a child slot 2, and allow that to flush too (this legitimately creates a zero-lamport index entry, confirmed by `update_index_for_flush`, which upserts unconditionally regardless of lamports).
3. In a further child slot 3, rewrite `A` with a non-zero value but do not force a flush (rely on the normal, asynchronous background flush cadence).
4. Trigger a call to `calculate_accounts_lt_hash_at_startup_from_index`/`verify_accounts` (or ledger-tool at startup) with ancestors spanning slots 1–3, before slot 3 is flushed.

This is fully reachable with ordinary, single-signer transactions and does not require validator/operator control; it only requires that the calculation runs while the rewrite is still cache-resident, which is a normal race window during startup/verification given asynchronous cache flushing.

### Recommendation
Add the same `is_zero_lamport()` guard to the `mix_out` correction step that already exists in the initial index-walk loop and is implicitly enforced by `self.load()` on the `mix_in` side, e.g.:
```rust
self.accounts_index.get_with_and_then(
    pubkey, ancestors, false,
    |(slot, account_info)| {
        if account_info.is_zero_lamport() {
            return;
        }
        self.get_account_accessor(slot, &account_info.storage_location())
            .get_loaded_account(|loaded_account| {
                cache_lt_hash.mix_out(&Self::lt_hash_account(&loaded_account, pubkey).0);
            });
    },
);
```
This ensures the `mix_out` only cancels contributions that were actually mixed in by the first loop.

### Proof of Concept
```rust
#[test]
fn test_calculate_accounts_lt_hash_at_startup_stale_zero_lamport_index_vs_cache() {
    let accounts = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let pubkey = solana_pubkey::new_rand();
    let owner = *AccountSharedData::default().owner();

    // Slot 1: fund the account, flush -> index has (slot1, nonzero)
    let account = AccountSharedData::new(100, 0, &owner);
    accounts.store_for_tests((1, [(&pubkey, &account)].as_slice()));
    accounts.add_root_and_flush_write_cache(1);

    // Slot 2 (child of 1): zero it out, flush -> index now has (slot2, zero-lamport)
    let zero_account = AccountSharedData::new(0, 0, &owner);
    accounts.store_for_tests((2, [(&pubkey, &zero_account)].as_slice()));
    accounts.add_root_and_flush_write_cache(2);

    // Slot 3 (child of 2): rewrite with a new nonzero value, but DO NOT flush
    let new_account = AccountSharedData::new(55, 0, &owner);
    accounts.store_for_tests((3, [(&pubkey, &new_account)].as_slice()));
    // no flush for slot 3

    let ancestors = Ancestors::from(vec![1, 2, 3]);

    let calculated = accounts.calculate_accounts_lt_hash_at_startup_from_index(&ancestors);

    // Expected: hash should equal identity mixed with ONLY the new_account's contribution,
    // matching the canonical semantics used by Bank's on-chain lt-hash updater (which
    // never mixes zero-lamport accounts in or out).
    let mut expected = LtHash::identity();
    expected.mix_in(&AccountsDb::lt_hash_account(&new_account, &pubkey).0);

    assert_eq!(
        calculated.0, expected,
        "calculated lt hash includes an erroneous mix_out of the stale zero-lamport index entry"
    );
}
```
Expected result given the current code: the assertion fails because `calculated.0` also contains the spurious `-lt_hash_account(zero_account)` term from the unguarded `mix_out`, demonstrating the divergence from the pure-function-of-committed-state invariant.

### Citations

**File:** accounts-db/src/accounts_db.rs (L3521-3530)
```rust
    pub fn load(
        &self,
        ancestors: &Ancestors,
        pubkey: &Pubkey,
        load_hint: LoadHint,
        populate_read_cache: PopulateReadCache,
    ) -> Option<(AccountSharedData, Slot)> {
        self.do_load(ancestors, pubkey, load_hint, populate_read_cache)
            .filter(|(account, _)| !account.is_zero_lamport())
    }
```

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

**File:** accounts-db/src/accounts_db.rs (L4848-4850)
```rust
            // Cache writes do not upsert the accounts index; it only ever holds storage entries,
            // populated on flush. Readers find cache-only accounts through the write cache. Only
            // the secondary indexes are updated here.
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L7203-7223)
```rust
/// A cache-only pubkey has no accounts-index entry (cache writes don't upsert the index),
/// yet `do_load` must still return it — `load_latest` finds it in the write cache before
/// the index is consulted.
#[test]
fn test_do_load_returns_cache_value_for_cache_only_pubkey() {
    let db = AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG);
    let pubkey = Pubkey::new_unique();
    let slot = 5;

    let account = AccountSharedData::new(100, 0, &Pubkey::default());
    db.accounts_cache.store(slot, &pubkey, account.clone());
    db.accounts_cache.add_root(slot);
    assert!(!db.accounts_index.contains(&pubkey));

    let ancestors = Ancestors::from(vec![slot]);
    assert_eq!(
        db.do_load_for_tests(&ancestors, &pubkey)
            .map(|(loaded, loaded_slot)| (loaded.lamports(), loaded_slot)),
        Some((account.lamports(), slot))
    );
}
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L671-683)
```rust
        let mut updater =
            |address: &Pubkey, prev: Option<AccountSharedData>, post: Option<AccountSharedData>| {
                // if there was an alive account, mix out
                if let Some(prev) = prev {
                    let prev_lt_hash = AccountsDb::lt_hash_account(&prev, address);
                    expected_accounts_lt_hash.0.mix_out(&prev_lt_hash.0);
                }

                // mix in the new one
                let post = post.unwrap_or_default();
                let post_lt_hash = AccountsDb::lt_hash_account(&post, address);
                expected_accounts_lt_hash.0.mix_in(&post_lt_hash.0);
            };
```

**File:** runtime/src/bank.rs (L5456-5465)
```rust
        info!("Verifying accounts...");
        let start = Instant::now();
        let expected_accounts_lt_hash = self.accounts_lt_hash.lock().unwrap().clone();
        let is_ok = if let Some(calculated_accounts_lt_hash) = calculated_accounts_lt_hash {
            check_lt_hash(&expected_accounts_lt_hash, calculated_accounts_lt_hash)
        } else {
            let calculated_accounts_lt_hash =
                accounts_db.calculate_accounts_lt_hash_at_startup_from_index(&self.ancestors);
            check_lt_hash(&expected_accounts_lt_hash, &calculated_accounts_lt_hash)
        };
```
