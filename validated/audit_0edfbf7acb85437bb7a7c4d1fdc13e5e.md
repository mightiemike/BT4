### Title
Capitalization/accounts-data-size delta computed from a pre-write snapshot read instead of the actual post-store balance change - (File: runtime/src/bank.rs)

### Summary
`Bank::store_account_and_update_capitalization` computes how much to adjust `self.capitalization` (and later, the accounts-data-size delta) by reading an "old" version of the account *before* the store, diffing its lamports/size against the incoming `new_account`, and then applying that delta. This is exactly the pattern flagged in the referenced report: instead of verifying the actual balance/size change that results from the write, the code assumes the delta implied by a separately-read "before" value is what will actually land in storage.

### Finding Description [1](#0-0) 

```
pub(crate) fn store_account_and_update_capitalization(
    &self,
    pubkey: &Pubkey,
    new_account: &AccountSharedData,
) {
    let old_account_data_size = if let Some(old_account) =
        self.get_account_with_fixed_root_no_cache(pubkey)
    {
        match new_account.lamports().cmp(&old_account.lamports()) {
            ... self.capitalization.fetch_add(diff, Relaxed) / fetch_sub(diff, Relaxed) ...
        }
        old_account.data().len()
    } else {
        self.capitalization.fetch_add(new_account.lamports(), Relaxed);
        0
    };

    self.store_account(pubkey, new_account);
    ...
    self.calculate_and_update_accounts_data_size_delta_off_chain(
        old_account_data_size,
        new_account_data_size,
    );
}
```

The `old_account` used for the diff is fetched via `get_account_with_fixed_root_no_cache`, i.e. a lookup that (per its name) is meant to bypass the accounts write cache and read the fixed-root, committed version of the account. The capitalization/data-size delta is derived entirely from this pre-write read, then `self.store_account(pubkey, new_account)` performs the actual write afterward. There is no verification that the value read as "old" is actually the value being overwritten by the subsequent store — the two operations (read-before, write-after) are not atomic with respect to any other update path.

This mirrors the report's root cause precisely: the code assumes `new_amount - old_amount` equals the real change in state rather than measuring the actual change caused by the store. In the ERC-20 report, the mismatch was caused by a token contract not honoring the exact `transferFrom` amount; here, the mismatch can be caused by `store_account_and_update_capitalization` being called multiple times for the same pubkey within the same slot (which the codebase's own test at [2](#0-1)  explicitly exercises — "Processing the invalid account again must not subtract the delegation twice") or by other stores to the same pubkey happening through the write-cache path (`store_accounts_without_stakes_cache` / `store_account`) between the "no_cache" read and this function's own store. Because the "before" value is captured via a separate no-cache read rather than an atomic compare against what is actually replaced in storage, out-of-order or repeated calls can cause capitalization and `accounts_data_size` to diverge from the true aggregate account state.

### Impact Explanation
`capitalization` and the off-chain `accounts_data_size` tracked here are consensus-relevant bank state: capitalization is checked against `calculate_capitalization_for_tests`/`calculate_capitalization_at_startup_from_index` (see [3](#0-2)  and [4](#0-3) , which sum every account's real lamports from the index/cache). If `store_account_and_update_capitalization` is invoked with stale "before" data — e.g., through re-entrant/duplicate calls for a pubkey already modified in the same slot, or a race with a concurrent write-cache store to the same pubkey — the tracked `capitalization` diverges from the value obtained by independently summing account balances, producing a silent, incorrect capitalization/accounts-data-size value that is baked into the bank state without an explicit mismatch check (unlike the mitigation the report recommends, which would re-read balance *after* the operation and assert the values match).

### Likelihood Explanation
`store_account_and_update_capitalization` is a low-level primitive used by bank internals wherever lamports need to be forcibly issued/burned outside of normal transaction processing (sysvar updates, builtin/precompiled account management, core-BPF migration capitalization bookkeeping, warmup/rewards test harnesses touching stake/vote accounts). The existing regression test at [5](#0-4)  shows the authors were already aware that calling this function twice for the same pubkey is a real, exercised scenario, and had to special-case it — indicating the underlying "diff from stale before-state" pattern is fragile and only correct as long as callers guarantee no intervening or duplicate mutation of the same pubkey between the no-cache read and the store. Any code path that violates that invariant (a plausible mistake given the many call sites, including migration and rewards-distribution logic) triggers the divergence without any runtime assertion catching it.

### Recommendation
Apply the same fix pattern the report recommends: instead of computing the delta from a separately-fetched "before" snapshot, compute it from the actual state transition guaranteed by the store — i.e., perform the store first (or under a lock that serializes read-modify-write for the pubkey), then diff against the value that was truly replaced, or add an explicit post-write consistency check (e.g., periodically assert `capitalization == calculate_capitalization_for_tests()` in non-test builds, or make the read-diff-store sequence atomic under the same lock used by `store_accounts_par`/`accounts_cache`). At minimum, document and enforce (with an assertion) that `store_account_and_update_capitalization` must never be called more than once per pubkey per slot, and audit all call sites (builtins, precompiles, core-BPF migration, sysvar updates) for compliance.

### Proof of Concept
Not independently reproducible from static analysis alone; the codebase's own test demonstrates the exact hazard condition: [6](#0-5)  calls `bank0.store_account_and_update_capitalization` twice in succession for the same `stake_pubkey`, and the test author's comment ("Processing the invalid account again must not subtract the delegation twice") confirms that without careful caller discipline, the second call would use a stale/incorrect "old" read and double-count the capitalization delta. Further dynamic verification (e.g., interleaving a write-cache store to the same pubkey between the no-cache read and the store inside a race harness) would require running a background Devin session with repo access, since this is beyond what can be confirmed via static code reading alone.

### Citations

**File:** runtime/src/bank.rs (L4819-4865)
```rust
    /// Technically this issues (or even burns!) new lamports,
    /// so be extra careful for its usage
    pub(crate) fn store_account_and_update_capitalization(
        &self,
        pubkey: &Pubkey,
        new_account: &AccountSharedData,
    ) {
        let old_account_data_size = if let Some(old_account) =
            self.get_account_with_fixed_root_no_cache(pubkey)
        {
            match new_account.lamports().cmp(&old_account.lamports()) {
                std::cmp::Ordering::Greater => {
                    let diff = new_account.lamports() - old_account.lamports();
                    trace!("store_account_and_update_capitalization: increased: {pubkey} {diff}");
                    self.capitalization.fetch_add(diff, Relaxed);
                }
                std::cmp::Ordering::Less => {
                    let diff = old_account.lamports() - new_account.lamports();
                    trace!("store_account_and_update_capitalization: decreased: {pubkey} {diff}");
                    self.capitalization.fetch_sub(diff, Relaxed);
                }
                std::cmp::Ordering::Equal => {}
            }
            old_account.data().len()
        } else {
            trace!(
                "store_account_and_update_capitalization: created: {pubkey} {}",
                new_account.lamports()
            );
            self.capitalization
                .fetch_add(new_account.lamports(), Relaxed);
            0
        };

        self.store_account(pubkey, new_account);

        // If the new account has zero lamports, that means it is being closed.
        let new_account_data_size = if new_account.lamports() == 0 {
            0
        } else {
            new_account.data().len()
        };
        self.calculate_and_update_accounts_data_size_delta_off_chain(
            old_account_data_size,
            new_account_data_size,
        );
    }
```

**File:** runtime/src/bank/tests.rs (L5585-5612)
```rust
                0 => {
                    // Remove a snapshot-backed delegation, leaving a pending removal.
                    let mut removed_stake_account = AccountSharedData::default();
                    removed_stake_account.set_owner(solana_stake_interface::program::id());
                    bank0.store_account_and_update_capitalization(
                        &stake_pubkey,
                        &removed_stake_account,
                    );
                    let delegated_stake_after_removal = bank0
                        .stakes_cache
                        .stakes()
                        .vote_accounts()
                        .get_delegated_stake(&vote_pubkey);

                    // Processing the invalid account again must not subtract the delegation twice.
                    bank0.store_account_and_update_capitalization(
                        &stake_pubkey,
                        &removed_stake_account,
                    );
                    assert_eq!(
                        bank0
                            .stakes_cache
                            .stakes()
                            .vote_accounts()
                            .get_delegated_stake(&vote_pubkey),
                        delegated_stake_after_removal,
                    );
                }
```

**File:** runtime/src/bank/tests.rs (L6067-6100)
```rust
fn test_add_builtin_account_squatted_while_not_replacing() {
    for pass in 0..3 {
        let (genesis_config, mint_keypair) = create_genesis_config(100_000);
        let bank = Bank::new_for_tests(&genesis_config);
        let program_id = solana_pubkey::new_rand();

        // someone managed to squat at program_id!
        bank.withdraw(&mint_keypair.pubkey(), 10).unwrap();
        if pass == 0 {
            add_root_and_flush_write_cache(&bank);
            assert_ne!(
                bank.capitalization(),
                bank.calculate_capitalization_for_tests()
            );
            continue;
        }
        test_utils::deposit(&bank, &program_id, 10).unwrap();
        if pass == 1 {
            add_root_and_flush_write_cache(&bank);
            assert_eq!(
                bank.capitalization(),
                bank.calculate_capitalization_for_tests()
            );
            continue;
        }

        bank.add_builtin_account("mock_program", &program_id);
        add_root_and_flush_write_cache(&bank);
        assert_eq!(
            bank.capitalization(),
            bank.calculate_capitalization_for_tests()
        );
    }
}
```

**File:** accounts-db/src/accounts_db.rs (L4728-4798)
```rust
    /// Calculates the capitalization
    ///
    /// Panics if capitalization overflows a u64.
    ///
    /// Note, this is *very* expensive!  It walks the whole accounts index,
    /// account-by-account, summing each account's balance.
    ///
    /// Only intended to be called at startup by ledger-tool or tests.
    pub fn calculate_capitalization_at_startup_from_index(&self, ancestors: &Ancestors) -> u64 {
        let stored_lamports = |pubkey: &Pubkey| {
            self.accounts_index
                .get_with_and_then(pubkey, ancestors, false, |(slot, account_info)| {
                    (!account_info.is_zero_lamport()).then(|| {
                        self.get_account_accessor(slot, &account_info.storage_location())
                            .get_loaded_account(|loaded_account| loaded_account.lamports())
                            // SAFETY: The index said this pubkey exists, so
                            // there must be an account to load.
                            .unwrap()
                    })
                })
                .flatten()
                .unwrap_or(0)
        };

        let storage_capitialization = self
            .accounts_index
            .account_maps
            .par_iter()
            .map(|accounts_index_bin| {
                accounts_index_bin
                    .keys()
                    .into_iter()
                    .map(|pubkey| stored_lamports(&pubkey))
                    .try_fold(0, u64::checked_add)
            })
            .try_reduce(|| 0, u64::checked_add)
            .expect("capitalization cannot overflow");

        // Sum as i128 because there is potential (although unlikely) for the cache updates to
        // overflow i64::MAX. For example, if the cache has multiple transactions that transfer a
        // large amount of lamports from one account to another, it could sum all of the transfers
        // from accounts first, overflow i128. Wrapping logic could also handle this properly (ie.
        // come to the correct answer), but then detection of overflow would be broken.
        let cached_update = self
            .accounts_cache
            .cached_pubkeys()
            .iter()
            .map(|pubkey| {
                // subtract out whatever older version the index walk produced (if any)
                let stored_lamports = stored_lamports(pubkey);

                // add in the cached amount of lamports
                let cached_lamports = self
                    .load(
                        ancestors,
                        pubkey,
                        LoadHint::FixedMaxRoot,
                        PopulateReadCache::False,
                    )
                    .map(|(account, _slot)| account.lamports())
                    .unwrap_or(0);

                cached_lamports as i128 - stored_lamports as i128
            })
            .sum::<i128>();

        i128::from(storage_capitialization)
            .checked_add(cached_update)
            .and_then(|result| u64::try_from(result).ok())
            .expect("capitalization cannot overflow")
    }
```
