Confirmed: `write_accounts_to_cache` at `accounts-db/src/accounts_db.rs:5413-5461` explicitly deduplicates a batch by pubkey, keeping only the *last* occurrence and silently discarding earlier duplicate entries (`stats.num_duplicate_accounts_skipped`), while `store_accounts_without_stakes_cache` in `runtime/src/bank.rs:4796-4810` computes `update_bank_hash_stats` and `enqueue_off_chain_accounts_lt_hash_updates` over the **raw, non-deduplicated** `accounts` list *before* that write-cache dedup happens — and the only duplicate check for that off-chain path (`runtime/src/bank/accounts_lt_hash.rs:100-127`) is compiled out in release builds (`cfg!(debug_assertions)`). This is a solid, concrete match for the reported bug class (deterministic combination of duplicate/repeated inputs silently diverges from the persisted/expected state because the uniqueness precondition is only enforced in a non-production configuration).

### Title
Release-mode accounts lt-hash update silently double-counts duplicate pubkeys in a single off-chain store batch, diverging from AccountsDb's own write-cache deduplication - (File: runtime/src/bank/accounts_lt_hash.rs)

### Summary
`Bank::store_accounts_without_stakes_cache` feeds every entry of a caller-supplied `accounts` batch into `enqueue_off_chain_accounts_lt_hash_updates`, which is documented to require the caller guarantee no duplicate pubkeys, and only *checks* that invariant when `debug_assertions` is enabled. In release builds the check is compiled out entirely. Meanwhile, the actual account-storing path (`AccountsDb::write_accounts_to_cache`) independently and silently deduplicates the very same batch, keeping only the last entry per pubkey. If any caller of `store_accounts`/`store_account`/`store_accounts_without_stakes_cache` ever passes more than one entry for the same pubkey in one call (a violation that is invisible in production), the accounts lattice hash will reflect both stale and final versions of that account, while storage/index end up holding only the final version — producing a bank whose incrementally-tracked `accounts_lt_hash` no longer matches what would be recomputed from disk.

### Finding Description
`enqueue_off_chain_accounts_lt_hash_updates` explicitly documents the precondition and only verifies it under `cfg!(debug_assertions)`: [1](#0-0) 

For every account in the batch, regardless of duplicates, it loads the current on-disk/cache value as `prev_account` and enqueues a lattice-hash job that will `mix_out(prev)`/`mix_in(curr)`: [2](#0-1) 

This is invoked from the bank's storage path *before* the actual write happens: [3](#0-2) 

The physical write path, however, silently deduplicates the batch and keeps only the last entry for a given pubkey, explicitly tracking the count of skipped duplicates in `num_duplicate_accounts_skipped`: [4](#0-3) 

Because both duplicate entries are read against the *same* `prev_account` (neither store has committed yet) but each entry's own `curr_account` is mixed in, if two versions of the same pubkey are ever supplied in one batch, the lattice hash accumulates the lattice contribution of BOTH curr states plus a double `mix_out` of prev, whereas storage/index end up with only the final version. In debug/test builds this is caught by a `panic!`, but in release/production builds — the configuration actually running on mainnet/testnet validators — the check is entirely absent, so the divergence is silent.

### Impact Explanation
This produces a hash/capitalization divergence class of bug: the bank's incrementally maintained `accounts_lt_hash` (used for lattice-based accounts verification, cross-checked against `calculate_accounts_lt_hash_at_startup_from_index` on restart, see the round-trip tests in `runtime/src/bank/accounts_lt_hash.rs`) would no longer match the hash independently recomputed from the accounts index/storage. Depending on how/where the lt-hash is used (startup verification, snapshot generation, or future extensions to bank-hash inclusion), this can manifest as a startup verification failure/panic, or — more critically — allow the live bank's tracked hash to silently diverge from what an honest re-derivation from storage would produce, which is exactly the "honest-node snapshot-vs-replay mismatch" class called out in scope. It is a latent correctness gap because the only enforcement of the underlying invariant is a debug-only assertion that never runs on production validator builds.

### Likelihood Explanation
Today's known callers (`Bank::transfer`, single-account `store_account`, `store_stake_accounts_in_partition`) are believed to pass unique-pubkey batches, so this is not trivially triggerable by an external attacker today. However, this is a structural/regression risk rather than a one-off: the invariant ("no duplicates in `accounts`") is enforced by comment only (`runtime/src/bank.rs:4791-4796`, `4752-4756`) and by a debug-only check that has zero effect in release builds, so any future code change, edge case in reward/partition construction, or a caller processing overlapping account lists would silently corrupt the accounts lt hash without any runtime signal in production, and the bug would only surface (if at all) far downstream in an unrelated hash-mismatch investigation.

### Recommendation
Make the duplicate check in `enqueue_off_chain_accounts_lt_hash_updates` unconditional (or at minimum keep it in release builds guarded by a light-weight, always-on check), and/or add a canonical dedup step (mirroring `write_accounts_to_cache`'s "keep last occurrence per pubkey" behavior) directly inside `enqueue_off_chain_accounts_lt_hash_updates`, exactly as `enqueue_on_chain_accounts_lt_hash_updates` already does via its `seen_accounts` set. This removes reliance on caller discipline that is only checked in non-production builds.

### Proof of Concept
1. Call `bank.store_accounts((slot, &[(&pubkey, &account_v1), (&pubkey, &account_v2)][..]), None)` in a **release** build (no `debug_assertions`).
2. Observe `enqueue_off_chain_accounts_lt_hash_updates` (`runtime/src/bank/accounts_lt_hash.rs:95-170`) enqueues two lattice-hash jobs for `pubkey`, both loading the same pre-store `prev_account`, one mixing in `account_v1` and the other `account_v2`.
3. Observe `write_accounts_to_cache` (`accounts-db/src/accounts_db.rs:5427-5458`) stores only `account_v2` (the last entry) and increments `num_duplicate_accounts_skipped`.
4. After `bank.freeze()` → `finish_accounts_lt_hash_updates`, compare `bank.accounts_lt_hash` against `AccountsDb::calculate_accounts_lt_hash_at_startup_from_index` (as done in `test_calculate_accounts_lt_hash_at_startup_from_index`, `runtime/src/bank/accounts_lt_hash.rs:740-782`): the two will diverge because the incremental hash included `account_v1`'s contribution that was never actually persisted, while the debug-only guard in `test_enqueue_off_chain_accounts_lt_hash_updates_catches_duplicates` (`runtime/src/bank/accounts_lt_hash.rs:969-996`) that would have caught this only runs with `debug_assertions` enabled.

### Citations

**File:** runtime/src/bank/accounts_lt_hash.rs (L85-127)
```rust
    /// Enqueues the accounts lt hash updates for `accounts` to the accounts hasher thread pool.
    ///
    /// This fn is meant to be called by off-chain events, meaning we know/control `accounts`.
    /// Contrasting with `enqueue_on_chain_accounts_lt_hash_updates()`, this fn:
    /// - Does not deduplicate accounts, requiring the caller to ensure there are no duplicates.
    /// - Does not assume loading the previous version of accounts is fast,
    ///   e.g. when storing stake accounts as part of partitioned epoch rewards.
    ///
    /// If Some, `thread_pool_for_hashing_accounts` will be used
    /// to load the previous version of accounts in parallel.
    pub fn enqueue_off_chain_accounts_lt_hash_updates<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
        thread_pool_for_loading_accounts: Option<&ThreadPool>,
    ) {
        if cfg!(debug_assertions) {
            // if debug assertions are on, we will check for duplicates
            use ahash::HashSetExt as _;
            let mut seen_accounts = ahash::HashSet::with_capacity(accounts.len());
            let mut duplicate_pubkeys = ahash::HashSet::with_capacity(0); // assume no duplicates
            for index in 0..accounts.len() {
                let pubkey = accounts.pubkey(index);
                if !seen_accounts.insert(pubkey) {
                    // we've already seen this account, so add it to the duplicates list
                    duplicate_pubkeys.insert(pubkey);
                }
            }
            if !duplicate_pubkeys.is_empty() {
                let mut duplicate_accounts = ahash::HashMap::<_, Vec<_>>::default();
                for duplicate_pubkey in duplicate_pubkeys {
                    for index in 0..accounts.len() {
                        let pubkey = accounts.pubkey(index);
                        if pubkey == duplicate_pubkey {
                            duplicate_accounts
                                .entry(pubkey)
                                .or_default()
                                .push(accounts.account(index, |account| account.take_account()));
                        }
                    }
                }
                panic!("duplicate accounts were enqueued for hashing: {duplicate_accounts:?}");
            }
        }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L134-157)
```rust
        let load_then_enqueue = |index| {
            let address = accounts.pubkey(index);
            let prev_account = self
                .rc
                .accounts
                .load_with_fixed_root_do_not_populate_read_cache(&self.ancestors, address)
                .map(|(account, _slot)| account);
            let curr_account = accounts.account(index, |account| {
                (account.lamports() != 0).then(|| account.take_account())
            });
            if prev_account.is_none() && curr_account.is_none() {
                // the account was ephemeral; skip it
            } else {
                // the account was modified; enqueue this update
                async_progress.spawn(
                    thread_pool_for_hashing_accounts,
                    AccountsLtHashUpdate {
                        address: *address,
                        prev_account,
                        curr_account,
                    },
                );
            }
        };
```

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

**File:** accounts-db/src/accounts_db.rs (L5408-5436)
```rust
    // Stores accounts in the write cache. If an account is zero-lamport and not present in the
    // cache or index, there is no need to store it in the write cache as it will not affect the
    // accounts hash. The function returns a BitVec indicating whether each account was stored in
    // the cache. Ordering of accounts is important as duplicate pubkeys are possible. The last
    // account in accounts_and_meta_to_store for each pubkey is stored in the write cache.
    fn write_accounts_to_cache<'a, 'b>(
        &self,
        slot: Slot,
        accounts_and_meta_to_store: &impl StorableAccounts<'b>,
        ancestors: &Ancestors,
    ) -> (BitVec, WriteAccountsToCacheStats) {
        let len = accounts_and_meta_to_store.len();
        let mut pubkey_set = HashSet::with_capacity_and_hasher(len, PubkeyHasherBuilder::default());
        let mut stats = WriteAccountsToCacheStats {
            num_initial_accounts_to_store: len as u64,
            ..Default::default()
        };
        let mut store_account = BitVec::new_fill(false, len as u64);

        (0..len).rev().for_each(|index| {
            accounts_and_meta_to_store.account_default_if_zero_lamport(index, |account| {
                let pubkey = account.pubkey();
                let is_duplicate_account = !pubkey_set.insert(*pubkey);
                if is_duplicate_account {
                    // If the same account is written multiple times in the same batch,
                    // only store the latest version
                    stats.num_duplicate_accounts_skipped += 1;
                    return;
                }
```
