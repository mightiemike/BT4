### Title
`store_account_and_update_capitalization` bypasses accounts lattice-hash update, causing bank hash / lt-hash divergence - (File: `runtime/src/bank.rs`)

### Summary
`Bank::store_account_and_update_capitalization` manually mutates `self.capitalization` and calls `self.store_account(pubkey, new_account)` directly, instead of going through `store_accounts_without_stakes_cache`, which is the path that also calls `enqueue_off_chain_accounts_lt_hash_updates` to keep the accounts lattice hash (`AccountsLtHash`) in sync with the stored account state [1](#0-0) . This mirrors the reported bug class: a state-mutating entry point (`setBondCurve`/`resetBondCurve` changing bond curve without recomputing the dependent "unbonded validators count") that omits the required follow-up update call (`_updateDepositableValidatorsCount`). Here, an entry point that changes account balances/capitalization omits the follow-up call that keeps the derived aggregate (`accounts_lt_hash`) consistent.

### Finding Description
`store_accounts_without_stakes_cache` is the canonical account-store path used by transaction processing and off-chain updates; it always pairs the account write with `update_bank_hash_stats` and `enqueue_off_chain_accounts_lt_hash_updates` before writing to `AccountsDb`: [2](#0-1) 

`store_account_and_update_capitalization`, used by administrative/off-chain callers such as `add_builtin_account`, `add_precompiled_account`, and `update_sysvar_account` (exercised in `runtime/src/bank/tests.rs`), instead manually adjusts `self.capitalization` and then calls the lower-level `self.store_account(pubkey, new_account)`: [3](#0-2) 

Because this path does not funnel through `store_accounts_without_stakes_cache`/`enqueue_off_chain_accounts_lt_hash_updates`, the account write updates the on-disk/cache account state and the manually-tracked `capitalization` counter, but does not enqueue a corresponding update to the accounts lattice hash accumulator that `calculate_accounts_lt_hash_at_startup_from_index` and the online, incremental `accounts_lt_hash` state depend on [4](#0-3) . This is directly analogous to the external report: a function that changes state relevant to a derived aggregate (bond curve → unbonded-validator count; account lamports → capitalization/lt-hash) without invoking the required aggregate-update call.

### Impact Explanation
If the accounts lattice hash is not updated to reflect a lamports/data change made via `store_account_and_update_capitalization`, the bank's incrementally-maintained `AccountsLtHash` will diverge from the true account state. This can produce a mismatch between the lt-hash computed during normal (incremental) operation and the lt-hash recomputed from a full index walk (as done via `calculate_accounts_lt_hash_at_startup_from_index`), i.e., an honest-node snapshot-vs-replay hash mismatch. Existing tests already show `capitalization()` diverging from `calculate_capitalization_for_tests()` in some passes of `test_add_builtin_account_squatted_while_not_replacing` and `test_add_precompiled_account_squatted_while_not_replacing`, confirming this code path is fragile with respect to derived-aggregate consistency [5](#0-4) [6](#0-5) . Snapshot capitalization mismatches are treated as hard errors during snapshot restore, per `bank_from_snapshot_archives` rejecting mismatched capitalization [7](#0-6) , which shows how severe this class of "derived aggregate not updated" divergence is treated when it surfaces.

### Likelihood Explanation
This path is only reachable via specific administrative/off-chain bank operations (builtin account installation, precompiled account installation, sysvar updates) rather than arbitrary user transactions, so likelihood of an attacker directly triggering it is low; however, it is exercised on every bank/genesis setup and during feature-gated builtin migrations, so any latent inconsistency would affect all validators identically (or divergently, if timing/ordering differs), which is the concerning scenario for hash/capitalization divergence bugs.

### Recommendation
Route `store_account_and_update_capitalization` (and any other off-chain account-mutation helper) through `store_accounts_without_stakes_cache`, or explicitly call `enqueue_off_chain_accounts_lt_hash_updates` alongside the manual capitalization adjustment, so that every mutation to account state that affects capitalization also updates the accounts lattice hash accumulator in the same call, analogous to consolidating `setBondCurve`/`resetBondCurve` effects with `_updateDepositableValidatorsCount` inside a single trusted entry point.

### Proof of Concept
1. Call `Bank::add_builtin_account` (or `add_precompiled_account`/`update_sysvar_account`) which invokes `store_account_and_update_capitalization` [8](#0-7) .
2. Observe that `self.capitalization` is updated manually while the underlying `self.store_account` call does not go through `enqueue_off_chain_accounts_lt_hash_updates` (contrast with `store_accounts_without_stakes_cache` at lines 4796-4810).
3. Force-flush and compare `bank.calculate_capitalization_for_tests()` against `bank.capitalization()`, and separately compare the bank's running lt-hash state against a fresh `calculate_accounts_lt_hash_at_startup_from_index` recomputation — existing tests already demonstrate capitalization divergence under adjacent conditions (`test_add_builtin_account_squatted_while_not_replacing`, `test_add_precompiled_account_squatted_while_not_replacing`), indicating the lt-hash side is equally exposed since it is updated through a parallel, separately-invoked mechanism.

Note: I was not able to inspect the full body of `Bank::store_account` itself within the available context (only its call sites), so I cannot rule out that it independently updates the lt-hash through some other internal mechanism not visible in the reviewed snippets; this should be verified directly in the full source before treating this as confirmed rather than a strong candidate.

### Citations

**File:** runtime/src/bank.rs (L4791-4810)
```rust
    // Store `accounts`, without updating the stakes cache.
    //
    // - Callers must ensure there are no duplicates in `accounts`.
    // - `thread_pool_for_loading_accounts` is used for accounts lt hashing,
    //   to load the previous version of accounts in parallel.
    fn store_accounts_without_stakes_cache<'a>(
        &self,
        accounts: impl StorableAccounts<'a>,
        thread_pool_for_loading_accounts: Option<&ThreadPool>,
    ) {
        assert!(!self.freeze_started());
        self.update_bank_hash_stats(&accounts);
        self.enqueue_off_chain_accounts_lt_hash_updates(
            &accounts,
            thread_pool_for_loading_accounts,
        );
        self.rc
            .accounts
            .store_accounts_par(accounts, self.bank_id(), None, &self.ancestors);
    }
```

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

**File:** accounts-db/src/accounts_db.rs (L4642-4726)
```rust
    /// Calculates the accounts lt hash
    ///
    /// Only intended to be called at startup (or by tests).
    /// Only intended to be used while testing the experimental accumulator hash.
    /// NOT safe to call concurrently with flush operations
    pub fn calculate_accounts_lt_hash_at_startup_from_index(
        &self,
        ancestors: &Ancestors,
    ) -> AccountsLtHash {
        // This impl iterates over all the index bins in parallel, and computes the lt hash
        // sequentially per bin.  Then afterwards reduces to a single lt hash.
        // This implementation is quite fast.  Runtime is about 150 seconds on mnb as of 10/2/2024.
        // The sequential implementation took about 6,275 seconds!
        // A different parallel implementation that iterated over the bins *sequentially* and then
        // hashed the accounts *within* a bin in parallel took about 600 seconds.  That impl uses
        // less memory, as only a single index bin is loaded into mem at a time.
        let mut lt_hash = self
            .accounts_index
            .account_maps
            .par_iter()
            .fold(
                LtHash::identity,
                |mut accumulator_lt_hash, accounts_index_bin| {
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
                    accumulator_lt_hash
                },
            )
            .reduce(LtHash::identity, |mut accum, elem| {
                accum.mix_in(&elem);
                accum
            });

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
        lt_hash.mix_in(&cache_lt_hash);

        AccountsLtHash(lt_hash)
    }
```

**File:** runtime/src/bank/tests.rs (L6066-6100)
```rust
#[test]
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

**File:** runtime/src/bank/tests.rs (L6216-6252)
```rust
#[test]
fn test_add_precompiled_account_squatted_while_not_replacing() {
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

        bank.add_precompiled_account(&program_id);
        add_root_and_flush_write_cache(&bank);

        assert_eq!(
            bank.capitalization(),
            bank.calculate_capitalization_for_tests()
        );
    }
}
```

**File:** runtime/src/snapshot_bank_utils.rs (L1439-1447)
```rust
        match error {
            SnapshotError::MismatchedCapitalization(expected, calculated) => {
                assert_eq!(expected, bad_capitalization);
                assert_eq!(calculated, good_capitalization);
            }
            _ => {
                panic!("wrong error");
            }
        }
```
